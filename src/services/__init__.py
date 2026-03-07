"""
核心服务层

提供与具体插件系统无关的核心业务服务。
内部模块（chat、dream、memory 等）应直接使用此层，
而 plugin_system.apis 仅作为面向插件的薄包装。
"""
