class DomainError(Exception):
    """Base dos erros de domínio; as cascas (REST/MCP/web) os traduzem para respostas."""


class NotFound(DomainError):
    pass


class Conflict(DomainError):
    pass


class InvalidState(DomainError):
    pass
