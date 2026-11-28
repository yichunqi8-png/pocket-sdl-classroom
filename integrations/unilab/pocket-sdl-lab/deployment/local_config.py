"""本地运行配置（仅包含非敏感设置；密钥须通过环境变量注入，不得写入代码）。"""


class BasicConfig:
    disable_browser = True
    no_update_feedback = True
    log_level = "INFO"


LOG_LEVEL = "INFO"
AUTO_OPEN_BROWSER = False
AK = ""
SK = ""
