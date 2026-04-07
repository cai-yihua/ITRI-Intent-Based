from typing import Any, Generator

from dify_plugin.interfaces.tool import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._base import call_n8n, ENDPOINTS


class DisableQoeTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage]:
        credentials = self.runtime.credentials

        ueid = tool_parameters["ueid"]

        payload = {
            "ueid": str(ueid),
        }

        result = call_n8n(
            credentials=credentials,
            endpoint_path=ENDPOINTS["disable_qoe"],
            method="POST",
            payload=payload,
        )

        yield self.create_text_message(text=result)
