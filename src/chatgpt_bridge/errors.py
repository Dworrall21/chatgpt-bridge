"""Bridge error taxonomy matching the design's failure codes.

None of these may trigger a fallback to general Chat.
"""


class BridgeError(Exception):
    """Base error. `code` matches the design's failure-code vocabulary."""

    code = "BRIDGE_ERROR"
    retryable = False
    stage = "unknown"

    def __init__(self, message: str, *, details_hash: str | None = None):
        super().__init__(message)
        self.message = message
        self.details_hash = details_hash

    def to_envelope(self) -> dict:
        return {
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
            "safe_to_retry": self.retryable,
            "details_hash": self.details_hash,
        }


class AppNotRunningError(BridgeError):
    code = "APP_NOT_RUNNING"
    retryable = True
    stage = "app"


class CdpUnavailableError(BridgeError):
    code = "CDP_UNAVAILABLE"
    retryable = True
    stage = "app"


class AppIdentityMismatchError(BridgeError):
    code = "APP_IDENTITY_MISMATCH"
    retryable = False
    stage = "app"


class AccountMismatchError(BridgeError):
    code = "ACCOUNT_MISMATCH"
    retryable = False
    stage = "project"


class ProjectNotFoundError(BridgeError):
    code = "PROJECT_NOT_FOUND"
    retryable = False
    stage = "project"


class ProjectAmbiguousError(BridgeError):
    code = "PROJECT_AMBIGUOUS"
    retryable = False
    stage = "project"


class ProjectIdChangedError(BridgeError):
    code = "PROJECT_ID_CHANGED"
    retryable = False
    stage = "project"


class ProjectContextChangedError(BridgeError):
    code = "PROJECT_CONTEXT_CHANGED"
    retryable = False
    stage = "project"


class ProjectAssertionFailedError(BridgeError):
    code = "PROJECT_ASSERTION_FAILED"
    retryable = False
    stage = "project"


class ProjectConfinementBreachError(BridgeError):
    code = "PROJECT_CONFINEMENT_BREACH"
    retryable = False
    stage = "project"


class WorkModeUnavailableError(BridgeError):
    code = "WORK_MODE_UNAVAILABLE"
    retryable = False
    stage = "mode"


class DestinationAssertionFailedError(BridgeError):
    code = "DESTINATION_ASSERTION_FAILED"
    retryable = False
    stage = "mode"


class ModelUnavailableError(BridgeError):
    code = "MODEL_UNAVAILABLE"
    retryable = False
    stage = "model"


class EffortUnavailableError(BridgeError):
    code = "EFFORT_UNAVAILABLE"
    retryable = False
    stage = "model"


class ModelAssertionFailedError(BridgeError):
    code = "MODEL_ASSERTION_FAILED"
    retryable = False
    stage = "model"


class ModelFallbackDetectedError(BridgeError):
    code = "MODEL_FALLBACK_DETECTED"
    retryable = False
    stage = "model"


class ConversationMetadataUnavailableError(BridgeError):
    code = "CONVERSATION_METADATA_UNAVAILABLE"
    retryable = False
    stage = "conversation"


class SendAmbiguousError(BridgeError):
    code = "SEND_AMBIGUOUS"
    retryable = False
    stage = "send"


class CompletionTimeoutError(BridgeError):
    code = "COMPLETION_TIMEOUT"
    retryable = True
    stage = "completion"


class UserInterferenceError(BridgeError):
    code = "USER_INTERFERENCE"
    retryable = False
    stage = "interference"


class ToolApprovalRequestedError(BridgeError):
    code = "TOOL_APPROVAL_REQUESTED"
    retryable = False
    stage = "completion"


class DuplicateSendDetectedError(BridgeError):
    code = "DUPLICATE_SEND_DETECTED"
    retryable = False
    stage = "send"


class ProtocolMismatchError(BridgeError):
    code = "PROTOCOL_MISMATCH"
    retryable = False
    stage = "protocol"


class AuthenticationFailedError(BridgeError):
    code = "AUTHENTICATION_FAILED"
    retryable = False
    stage = "auth"


class DeadlineExceededError(BridgeError):
    code = "DEADLINE_EXCEEDED"
    retryable = False
    stage = "deadline"


class PolicyRejectionError(BridgeError):
    code = "POLICY_REJECTED"
    retryable = False
    stage = "policy"


class SchemaRejectionError(BridgeError):
    code = "SCHEMA_REJECTED"
    retryable = False
    stage = "protocol"
