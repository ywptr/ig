"""IG tenant model-selection methods for the registered adapters."""
from ig_core.providers.base import ImageProviderConfigurationError
from ig_core.providers.video import VideoProviderConfigurationError

from app.media.model_governance import model_policy, resolve_model


class ImageModelPolicy:
    @classmethod
    def tenant_default_model(cls, config: dict) -> str | None:
        policy = model_policy(cls, config, "image")
        return policy.default if policy.source == "tenant_default" else None

    @classmethod
    def resolve_tenant_model(cls, config: dict, explicit_model: str | None = None) -> str:
        try:
            return resolve_model(cls, config, "image", explicit_model)
        except ValueError as exc:
            raise ImageProviderConfigurationError(str(exc)) from None

    @classmethod
    def default_model_for(cls, config: dict) -> str:
        return cls.resolve_tenant_model(config)

    @classmethod
    def validate_submission_options(cls, *, config, model, count, strategy, reference_count, parameters):
        selected = cls.resolve_tenant_model(config, model)
        cls.validate_model_options(
            model=selected, count=count, strategy=strategy,
            reference_count=reference_count, parameters=parameters,
        )


class VideoModelPolicy:
    @classmethod
    def resolve_tenant_model(cls, config: dict, explicit_model: str | None = None) -> str:
        try:
            return resolve_model(cls, config, "video", explicit_model)
        except ValueError as exc:
            raise VideoProviderConfigurationError(str(exc)) from None

    @classmethod
    def default_model_for(cls, config: dict) -> str:
        return cls.resolve_tenant_model(config)
