from dify_plugin import ToolProvider


class IntentAgentProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict) -> None:
        n8n_base_url = credentials.get("n8n_base_url", "")
        if not n8n_base_url:
            raise ValueError("n8n_base_url is required")
        if not n8n_base_url.startswith("http"):
            raise ValueError("n8n_base_url must start with http:// or https://")
