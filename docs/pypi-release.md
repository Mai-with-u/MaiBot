# 主程序自动发布

在 GitHub Actions 中手动运行 **Publish MaiBot**。顺序固定为：构建验证 → 保存原产物 → 上传 PyPI → 创建 GitHub Release。构建失败时不会上传包，也不会创建 tag 或 Release。

## 首次配置

1. 确认有权使用 PyPI 项目名 `maibot`。首次发布可在 PyPI 的 Publishing 页面配置 pending publisher；已有项目则在项目管理页添加 Trusted Publisher。
2. 在 GitHub 仓库创建名为 `maibot` 的 Environment。
3. 在 PyPI 配置 GitHub Trusted Publisher：Owner `Mai-with-u`、Repository `MaiBot`、Workflow `publish-maibot.yml`、Environment `maibot`。不需要沿用 WebUI 的 API Token。
4. 将工作流、辅助脚本及主程序打包实现提交并推送到仓库。手动触发工作流的分支必须包含这些文件。

参考：[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)、[首次创建项目](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)。本流程本身不修改 PyPI/GitHub 的账号权限设置。

## 每次发布

1. 先运行现有 **Prepare Main Release**，发布 Dashboard 正式包并更新主程序的正式依赖。
2. 确认待发布提交中的 `pyproject.toml` 使用目标正式版本；在 `changelogs/changelog.md` 中填写同版本发布说明，将待发布变更合入目标分支。
3. 运行 **Publish MaiBot**，填写 `version`（例如 `1.3.9`）、`ref`（建议填写完整提交 SHA，也支持分支或已有 tag）。第一次先保持 `publish` 未勾选，检查构建验证结果。
4. 正式发布时重新运行并勾选 `publish`。它发布该提交的包，随后创建 `v1.3.9` 的 tag 和 GitHub Release。Release 正文取自对应提交的 changelog，Python 安装包在 PyPI 提供。

本流程仅发布正式版本；开发版主程序和预发布 Release 暂不处理。当前源码依赖 Dashboard dev 包时，版本检查会明确失败，需要先完成正式依赖准备。

## 失败重试

- 构建/安装验证失败：没有对外发布，可以修复代码后保留版本号重新运行。
- 上传 PyPI 失败：在原 Actions run 中选择 **Re-run failed jobs**。使用原已验证的 wheel 和源码包，补传缺失文件。
- PyPI 成功、创建 Release 失败：同样重跑原 run 的失败 job，补建 Release。
- **Re-run all jobs** 也会优先复用同一 run 已保存的产物，不根据可能已经移动的分支重新打包。
- 若需要先修复发布脚本，再继续一个已经上传过包的版本：推送修复后启动新的 run，填写原 `version` 和 `resume_run_id`。流程复用原 run 的提交 SHA、发布说明及包文件，`ref` 不再用于构建。原产物缺失时明确失败，禁止重新构建。
- 产物保存 90 天。上传开始后应在原 run 中重试；不要对同一已发布版本重新构建。文件内容不同会被校验拒绝，即使文件名相同也不会跳过冲突。
- 若同名 tag 指向不同提交，或同名 Release 是草稿/预发布状态，流程会失败，不移动 tag 或修改已有 Release。

每个 run 的产物包含 wheel、源码包、版本、固定提交 SHA、文件 SHA256 和 Release 正文。构建与发布 job 分开；PyPI job 不执行待发布源码中的构建步骤，也不重新打包。

PyPA 上传 Action 会在包目录生成 `.publish.attestation` 签名证明。上传后仅对原 wheel 和源码包做 SHA256 检查，允许它们对应的签名证明；未知文件、额外发行包及原包内容变化仍会导致失败。

最后创建 Release 使用仓库的 `GITHUB_TOKEN`；由该 Token 创建的 tag/Release 不会触发普通下游工作流。如果后续需要把 Docker 或 OneKey 发布接到本流程，应显式编排，不能依赖这些 tag 的 push 事件。
