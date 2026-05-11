from typing import Any
import logging

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from dify_plugin.config.logger_format import plugin_logger_handler

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)


class Neo4jExcelImporterProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        uri  = credentials.get("neo4j_uri", "").strip()
        user = credentials.get("neo4j_user", "").strip()
        pwd  = credentials.get("neo4j_password", "").strip()

        if not uri:
            raise ToolProviderCredentialValidationError("neo4j_uri 不能为空")
        if not user:
            raise ToolProviderCredentialValidationError("neo4j_user 不能为空")
        if not pwd:
            raise ToolProviderCredentialValidationError("neo4j_password 不能为空")

        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(uri, auth=(user, pwd))
            driver.verify_connectivity()
            driver.close()
            logger.info("Neo4j connectivity verified: %s", uri)
        except Exception as e:
            raise ToolProviderCredentialValidationError(
                f"无法连接到 Neo4j（{uri}）：{e}"
            )
