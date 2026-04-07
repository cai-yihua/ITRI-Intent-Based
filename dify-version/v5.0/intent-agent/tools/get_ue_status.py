from typing import Any, Generator

from dify_plugin.interfaces.tool import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._base import call_n8n, ENDPOINTS


class GetUeStatusTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage]:
        credentials = self.runtime.credentials

        payload: dict[str, Any] = {}
        for key in ("ueid", "location", "all_edge", "all_center", "worst_part"):
            value = tool_parameters.get(key)
            if value is not None:
                payload[key] = value

        result = call_n8n(
            credentials=credentials,
            endpoint_path=ENDPOINTS["get_ue_status"],
            method="POST",
            payload=payload,
        )

        yield self.create_text_message(text=result)
