"""Tenant admission for normalized model selection. No provider calls."""
from app.artifacts.references import resolve_references, scoped_reference_artifacts
from app.media.model_governance import ModelGovernanceError, enabled_model_ids, model_is_current, model_policy
from app.media.providers.registry import MEDIA_PROVIDERS
from app.tenancy.provider_configs import list_provider_configs
from ig_core.media.selection import (
    EligibilityReason, ModelCandidate, ModelEligibility, ModelSelectionPolicy, ModelSelectionResult,
)
from ig_core.media.validation import (
    MediaRequestValidationError, validate_media_request, validate_model_capabilities,
    video_operation,
)


class ModelSelectionError(ValueError):
    def __init__(self, result: ModelSelectionResult):
        super().__init__("No technically eligible model matches the selection policy")
        self.result = result


def request_operation(request):
    if request.media_type == "video":
        return video_operation(ref.role for ref in request.references)
    if request.media_type == "audio":
        return "text_to_speech"
    return "image_to_image" if request.references else "text_to_image"


def technical_reason(db, request, adapter, model, reference_rows):
    technical = model.technical
    operation = request_operation(request)
    if not technical or operation not in technical.operations:
        return EligibilityReason(code="OPERATION_UNSUPPORTED", message="Model does not support the requested operation")
    if request.media_type == "video" and (
        technical.execution.mode != "asynchronous" or not technical.execution.polling_required
    ):
        return EligibilityReason(code="EXECUTION_UNSUPPORTED", message="Model uses an unsupported video lifecycle")
    try:
        validate_model_capabilities(request, model)
        if request.media_type == "image":
            adapter.validate_model_options(
                model=model.id, count=request.output.count, strategy=request.strategy,
                reference_count=len(request.references),
                parameters=request.advanced.parameters if request.advanced else {},
            )
    except MediaRequestValidationError as exc:
        return EligibilityReason(code=exc.code, message=str(exc))
    except (ValueError, TypeError, AttributeError):
        return EligibilityReason(code="MODEL_OPTIONS_UNSUPPORTED", message="Model does not support the requested options")
    if request.references:
        try:
            resolve_references(db, request.references, slots=technical.reference_inputs,
                               tenant_id=None, user_id=None, rows=reference_rows)
        except MediaRequestValidationError as exc:
            return EligibilityReason(code="REFERENCE_INCOMPATIBLE", message=str(exc))
    return None


def evaluate_model(db, request, provider, adapter, model, row, reference_rows):
    reasons = []
    config = row.config_json if row else None
    configured = bool(row and row.encrypted_credentials and isinstance(config, dict)
                      and adapter.configuration_is_valid(config))
    provider_enabled = bool(row and row.enabled)
    model_enabled = False
    if not configured:
        reasons.append(EligibilityReason(code="PROVIDER_NOT_CONFIGURED", message="Provider credentials or settings are missing"))
    if row and not provider_enabled:
        reasons.append(EligibilityReason(code="PROVIDER_DISABLED", message="Provider is disabled for this tenant"))
    if configured:
        try:
            model_enabled = model.id in enabled_model_ids(adapter, config, request.media_type)
        except ModelGovernanceError:
            model_enabled = False
            reasons.append(EligibilityReason(code="MODEL_POLICY_INVALID", message="Tenant model policy is invalid"))
        else:
            if not model_enabled:
                reasons.append(EligibilityReason(code="MODEL_DISABLED", message="Model is disabled for this tenant"))
    if not model_is_current(model):
        reasons.append(EligibilityReason(code="MODEL_SUNSET", message="Model is past its sunset date"))
    available = not reasons
    reason = technical_reason(db, request, adapter, model, reference_rows)
    if reason:
        reasons.append(reason)
    return ModelEligibility(candidate=ModelCandidate(provider=provider, model=model.id),
                            registered=True, configured=configured, enabled=provider_enabled and model_enabled,
                            available=available, compatible=reason is None, eligible=not reasons, reasons=reasons)


def unresolved_candidate(candidate, *, ambiguous=False):
    return ModelEligibility(candidate=candidate, registered=ambiguous, configured=False,
                            enabled=False, available=False, compatible=None, eligible=False,
                            reasons=[EligibilityReason(
                                code="MODEL_AMBIGUOUS" if ambiguous else "MODEL_NOT_REGISTERED",
                                message="Candidate must identify its provider" if ambiguous else "Model is not in the media catalog",
                            )])


def resolve_selection(db, request, *, tenant_id, user_id):
    validate_media_request(request)
    policy = request.selection_policy or ModelSelectionPolicy()
    if request.advanced and (request.advanced.provider or request.advanced.model):
        raise MediaRequestValidationError("Use selection_policy or legacy advanced provider/model fields")
    # Match execution normalization for the image path.
    if request.media_type == "image" and request.output.aspect_ratio is None:
        from app.media.service import resolve_aspect_ratio
        request = request.model_copy(update={
            "output": request.output.model_copy(update={"aspect_ratio": resolve_aspect_ratio(request)})})
    reference_rows = scoped_reference_artifacts(db, [ref.artifact_id for ref in request.references],
                                                tenant_id=tenant_id, user_id=user_id)
    configurations = {row.provider: row for row in list_provider_configs(db, tenant_id=tenant_id)}
    evaluations = []
    automatic = []
    defaults = set()
    tenant_defaults = set()
    for provider, adapter in MEDIA_PROVIDERS[request.media_type].items():
        row = configurations.get(provider)
        models = [evaluate_model(db, request, provider, adapter, model, row, reference_rows) for model in adapter.CAPABILITIES.models]
        evaluations.extend(models)
        if not row or not row.enabled or not any(model.configured for model in models):
            continue
        try:
            provider_policy = model_policy(adapter, row.config_json, request.media_type)
            default = provider_policy.default
            if provider_policy.source == "tenant_default":
                tenant_defaults.add((provider, default))
        except ModelGovernanceError:
            default = None
        defaults.add((provider, default))
        automatic.extend(sorted(models, key=lambda model: model.candidate.model != default))

    automatic.sort(key=lambda item: (item.candidate.provider, item.candidate.model) not in tenant_defaults)

    rejected = []
    for candidate in policy.candidates:
        matches = [item for item in evaluations if item.candidate.model == candidate.model
                   and (candidate.provider is None or item.candidate.provider == candidate.provider)]
        evaluated = matches[0] if len(matches) == 1 else unresolved_candidate(candidate, ambiguous=bool(matches))
        if evaluated.eligible:
            reason = "LOCKED_MODEL" if policy.mode == "LOCKED" else "PREFERRED_CANDIDATE"
            return ModelSelectionResult(policy=policy, selected=evaluated.candidate, reason=reason,
                                        evaluations=evaluations, rejected=rejected)
        rejected.append(evaluated)
    if policy.mode == "AUTO" or policy.allow_fallback:
        selected = next((item.candidate for item in automatic if item.eligible), None)
        if selected:
            is_default = (selected.provider, selected.model) in defaults
            reason = "AUTO_DEFAULT" if is_default and selected == automatic[0].candidate else "AUTO_ELIGIBLE_FALLBACK"
            if policy.mode == "AUTO" and selected != automatic[0].candidate:
                rejected = [automatic[0]]
            return ModelSelectionResult(policy=policy, selected=selected, reason=reason,
                                        fallback_used=policy.mode == "PREFERRED" or reason == "AUTO_ELIGIBLE_FALLBACK",
                                        evaluations=evaluations, rejected=rejected)
    if policy.mode == "AUTO":
        rejected = (automatic or evaluations)[:3]
    return ModelSelectionResult(policy=policy, selected=None, reason="NO_ELIGIBLE_MODEL",
                                evaluations=evaluations, rejected=rejected)


def accept_selection(db, request, *, tenant_id, user_id, accepted_candidate=None):
    result = resolve_selection(db, request, tenant_id=tenant_id, user_id=user_id)
    if result.selected is None and accepted_candidate is not None:
        # Only the workflow worker supplies a pin from a prior accepted Job.
        if result.policy.mode == "LOCKED" and result.policy.candidates == (accepted_candidate,):
            prior = next((item for item in result.evaluations if item.candidate == accepted_candidate), None)
            if prior and prior.reasons and all(reason.code in {
                "MODEL_SUNSET", "MODEL_DISABLED", "MODEL_POLICY_INVALID",
            } for reason in prior.reasons):
                result = result.model_copy(update={
                    "selected": accepted_candidate, "reason": "ACCEPTED_WORKFLOW_MODEL", "rejected": (),
                })
    if result.selected is None:
        raise ModelSelectionError(result)
    return result.selected, result.snapshot().model_dump(mode="json")


def legacy_snapshot(request, provider, model, reason):
    advanced = request.advanced
    selected = ModelCandidate(provider=provider, model=model)
    policy = ModelSelectionPolicy(
        mode="LOCKED", candidates=(selected,),
    ) if advanced and (advanced.provider or advanced.model) else ModelSelectionPolicy()
    return {"policy": policy.model_dump(mode="json"), "selected": selected.model_dump(mode="json"),
            "reason": reason, "fallback_used": False}
