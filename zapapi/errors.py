class ChatNotFoundException(Exception):
    """Raised when a chat cannot be found."""

    def __init__(self, target, message="Chat not found"):
        self.target = target
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f"{self.target} -> {self.message}"


class NoOpenChatException(Exception):
    """Raised when no chat is currently open."""

    def __init__(
        self,
        message=(
            "Nenhum chat aberto no momento. "
            "Use ZapAPI.chats.get(<chat_name>) ou passe um chat explicito "
            "para api.messages.*."
        ),
    ):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return self.message


class AuthenticationRequiredException(Exception):
    """Raised when WhatsApp Web requires an interactive login."""

    def __init__(
        self,
        message=(
            "Sessao do WhatsApp Web nao autenticada. "
            "Execute com headless=False uma vez, escaneie o QR Code "
            "e reutilize o mesmo diretorio de perfil nas proximas execucoes."
        ),
    ):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return self.message


class WhatsAppWebTimeoutException(Exception):
    """Raised when WhatsApp Web does not reach the expected UI state in time."""

    def __init__(self, message="Timeout aguardando o WhatsApp Web ficar pronto."):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return self.message
