from typing import Any, Generator

from dify_plugin.interfaces.tool import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._base import call_n8n, ENDPOINTS, build_im_payload


class EnableImTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage]:
        credentials = self.runtime.credentials

        payload = build_im_payload(tool_parameters)

        result = call_n8n(
            credentials=credentials,
            endpoint_path=ENDPOINTS["enable_im"],
            method="POST",
            payload=payload,
        )

        yield self.create_text_message(text=result)
