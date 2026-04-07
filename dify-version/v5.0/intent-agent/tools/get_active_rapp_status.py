from typing import Any, Generator

from dify_plugin.interfaces.tool import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._base import call_n8n, ENDPOINTS


class GetActiveRappStatusTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage]:
        credentials = self.runtime.credentials

        result = call_n8n(
            credentials=credentials,
            endpoint_path=ENDPOINTS["get_active_rapp_status"],
            method="POST",
        )

        yield self.create_text_message(text=result)
