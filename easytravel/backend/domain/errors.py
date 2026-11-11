"""Transport-independent business failures. Exception text is internal only."""


class BusinessError(Exception):
    pass


class ResourceNotFound(BusinessError):
    pass


class BusinessConflict(BusinessError):
    pass


class IdempotencyKeyReused(BusinessConflict):
    pass


class CommandInProgress(BusinessConflict):
    pass


class DependencyUnavailable(BusinessError):
    pass
