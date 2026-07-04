from pydantic import BaseModel, Field


class TokenVerifyRequest(BaseModel):
    token: str = Field(..., description="访问令牌")


class TokenVerifyResponse(BaseModel):
    valid: bool = Field(..., description="Token 是否有效")
    message: str = Field(..., description="验证结果消息")
    is_first_setup: bool = Field(False, description="是否为首次设置")
    token_source: str = Field("temporary", description="Token 来源")
    requires_custom_token: bool = Field(False, description="是否需要设置自定义 Token")


class TokenUpdateRequest(BaseModel):
    new_token: str = Field(..., description="新的访问令牌", min_length=10)


class TokenUpdateResponse(BaseModel):
    success: bool = Field(..., description="是否更新成功")
    message: str = Field(..., description="更新结果消息")


class TokenRegenerateResponse(BaseModel):
    success: bool = Field(..., description="是否生成成功")
    token: str = Field(..., description="新生成的令牌")
    message: str = Field(..., description="生成结果消息")


class FirstSetupStatusResponse(BaseModel):
    is_first_setup: bool = Field(..., description="是否为首次配置")
    token_source: str = Field(..., description="Token 来源")
    requires_custom_token: bool = Field(..., description="是否需要设置自定义 Token")
    message: str = Field(..., description="状态消息")


class CompleteSetupResponse(BaseModel):
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="结果消息")


class ResetSetupResponse(BaseModel):
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="结果消息")
