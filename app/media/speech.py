"""Tenant speech admission and execution through the provider-neutral contract."""
from app.db.database import SessionLocal
from app.jobs.cancellation import check_cancellation
from app.media.model_governance import model_policy, registered_model, validate_selected_model
from app.media.providers.registry import SPEECH_PROVIDERS
from app.tenancy.provider_configs import get_provider_config, get_provider_credentials, list_provider_configs
from ig_core.execution.result import Completed
from ig_core.media.speech import SpeechGenerationRequest, SpeechProviderError, resolve_model_speech


class SpeechRequestError(ValueError):
    """Invalid speech input or tenant selection."""


def speech_request(request):
    return SpeechGenerationRequest(text=request.intent.description, **request.output.model_dump())


def _resolve(adapter, config, model, request, level):
    metadata = validate_selected_model(adapter, config, "audio", model)
    return resolve_model_speech(metadata, request, level)


def select_speech(db, media_request, request, *, tenant_id):
    options = media_request.advanced
    provider_id = options.provider.strip().lower() if options and options.provider else None
    explicit_model = options.model if options else None
    rows = {row.provider: row for row in list_provider_configs(db, tenant_id=tenant_id)}
    for provider, adapter in SPEECH_PROVIDERS.items():
        if provider_id and provider != provider_id:
            continue
        row = rows.get(provider)
        if not row or not row.enabled or not row.encrypted_credentials or not adapter.configuration_is_valid(row.config_json):
            continue
        if explicit_model and not any(item.id == explicit_model for item in adapter.CAPABILITIES.models):
            if provider_id:
                raise ValueError("Unsupported speech model")
            continue
        model = explicit_model or model_policy(adapter, row.config_json, "audio").default
        if model:
            return {"provider": provider, "model": model}, _resolve(
                adapter, row.config_json, model, request, media_request.interface_level)
    raise ValueError("No enabled speech provider or model matches the request")


def execute_speech_generation(input_data: dict, *, tenant_id: str, user_id: str | None) -> Completed:
    if not tenant_id or not user_id:
        raise ValueError("Speech generation requires trusted ownership")
    request = SpeechGenerationRequest.model_validate(input_data["speech"])
    selection = input_data["selection"]
    provider, model = selection["provider"], selection["model"]
    with SessionLocal() as db:
        adapter_type = SPEECH_PROVIDERS.get(provider)
        row = get_provider_config(db, tenant_id=tenant_id, provider=provider)
        if not adapter_type or not row or not row.enabled or not adapter_type.configuration_is_valid(row.config_json):
            raise ValueError("Speech provider is unavailable")
        request = resolve_model_speech(registered_model(adapter_type, model), request, input_data["request"]["interface_level"])
        credentials = get_provider_credentials(db, tenant_id=tenant_id, provider=provider)
        adapter = adapter_type.from_config(credentials=credentials, config=row.config_json, model=model)
    check_cancellation()
    artifact = adapter.synthesize(request)
    if artifact.media_type != "audio":
        raise SpeechProviderError("SPEECH_INVALID_AUDIO: Provider returned a different media type")
    return Completed(provider=provider, model=model, artifact=artifact)
