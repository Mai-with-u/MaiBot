<img src="depends-data/maimai.png" alt="MaiBot" title="作者:略nd" width="300">

# 麦麦！MaiCore-MaiBot

![Python Version](https://img.shields.io/badge/Python-3.10+-blue)
![License](https://img.shields.io/github/license/SengokuCola/MaiMBot?label=协议)
![Status](https://img.shields.io/badge/状态-开发中-yellow)
![Contributors](https://img.shields.io/github/contributors/MaiM-with-u/MaiBot.svg?style=flat&label=贡献者)
![forks](https://img.shields.io/github/forks/MaiM-with-u/MaiBot.svg?style=flat&label=分支数)
![stars](https://img.shields.io/github/stars/MaiM-with-u/MaiBot?style=flat&label=星标数)
![issues](https://img.shields.io/github/issues/MaiM-with-u/MaiBot)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/DrSmoothl/MaiBot)

<div style="text-align: center">
<strong>
<a href="https://www.bilibili.com/video/BV1amAneGE3P">🌟 演示视频</a> | 
<a href="#-更新和安装">🚀 快速入门</a> | 
<a href="#-文档">📃 教程</a> | 
<a href="#-讨论">💬 讨论</a> | 
<a href="#-贡献和致谢">🙋 贡献指南</a>
</strong>
</div>

## 🎉 介绍

**🍔MaiCore 是一个基于大语言模型的可交互智能体**

- 💭 **拟人构建的prompt**：使用自然语言风格构建回复器的prompt，实现近似人类言语习惯的回复。
- 💭 **行为规划**：在合适的时间说话，使用合适的动作
- 🧠 **表达学习**：学习群友的说话风格和表达方式，学会真实人类的说话风格
- 🤔 **黑话学习**：自主的学习没有见过的词语，尝试理解并认知含义
- 🔌 **插件系统**：提供API和事件系统，可编写丰富插件。
- 💝 **情感表达**：情绪系统和表情包系统。

<div style="text-align: center">
<a href="https://www.bilibili.com/video/BV1amAneGE3P" target="_blank">
    <picture>
      <source media="(max-width: 600px)" srcset="depends-data/video.png" width="100%">
      <img src="depends-data/video.png" width="30%" alt="麦麦演示视频">
    </picture>
    <br />
  👆 点击观看麦麦演示视频 👆
</a>
</div>

## 🔥 更新和安装


**最新版本: v0.12.0** ([更新日志](changelogs/changelog.md))


可前往 [Release](https://github.com/MaiM-with-u/MaiBot/releases/) 页面下载最新版本

可前往 [启动器发布页面](https://github.com/MaiM-with-u/mailauncher/releases/)下载最新启动器

注意，启动器处于早期开发版本，仅支持MacOS

**GitHub 分支说明：**
- `main`: 稳定发布版本(推荐)

- `dev`: 开发测试版本(不稳定)

- `classical`: 经典版本(停止维护)

### 最新版本部署教程
- [🚀 最新版本部署教程](https://docs.mai-mai.org/manual/deployment/mmc_deploy_windows.html) - 基于 MaiCore 的新版本部署方式(与旧版本不兼容)

> [!WARNING]
> - 项目处于活跃开发阶段，功能和 API 可能随时调整。
> - 有问题可以提交 Issue 。
> - QQ 机器人存在被限制风险，请自行了解，谨慎使用。
> - 由于程序处于开发中，可能消耗较多 token。

## 💬 讨论

**技术交流群/答疑群：**
  [麦麦脑电图](https://qm.qq.com/q/RzmCiRtHEW) | 
  [麦麦大脑磁共振](https://qm.qq.com/q/VQ3XZrWgMs) | 
  [麦麦要当VTB](https://qm.qq.com/q/wGePTl1UyY) | 

  为了维持技术交流和互帮互助的氛围，请不要在技术交流群讨论过多无关内容~

**聊天吹水群：**
- [麦麦之闲聊群](https://qm.qq.com/q/JxvHZnxyec)

  麦麦相关闲聊群，此群仅用于聊天，提问部署/技术问题可能不会快速得到答案

**插件开发/测试版讨论群：**
- [插件开发群](https://qm.qq.com/q/1036092828)

  进阶内容，包括插件开发，测试版使用等等

## 📚 文档

**部分内容可能更新不够及时，请注意版本对应**

- [📚 核心 Wiki 文档](https://docs.mai-mai.org) - 项目最全面的文档中心，你可以了解麦麦有关的一切。


## 📚 衍生项目

### MaiCraft（早期开发）
[MaiCraft](https://github.com/MaiM-with-u/Maicraft)
> 让麦麦具有玩MC能力的项目
> 交流群：1058573197

### MoFox_Bot
[MoFox - 仓库地址](https://github.com/MoFox-Studio/MoFox-Core)
> MoFox_Bot 是一个基于 MaiCore 0.10.0 snapshot.5 的增强型 fork 项目
> 我们保留了原项目几乎所有核心功能，并在此基础上进行了深度优化与功能扩展，致力于打造一个更稳定、更智能、更具趣味性的 AI 智能体。



## 设计理念(原始时代的火花)

> **千石可乐说：**
> - 这个项目最初只是为了给牛牛 bot 添加一点额外的功能，但是功能越写越多，最后决定重写。其目的是为了创造一个活跃在 QQ 群聊的"生命体"。目的并不是为了写一个功能齐全的机器人，而是一个尽可能让人感知到真实的类人存在。
> - 程序的功能设计理念基于一个核心的原则："最像而不是好"。
> - 如果人类真的需要一个 AI 来陪伴自己，并不是所有人都需要一个完美的，能解决所有问题的"helpful assistant"，而是一个会犯错的，拥有自己感知和想法的"生命形式"。
> - 代码会保持开源和开放，但个人希望 MaiMbot 的运行时数据保持封闭，尽量避免以显式命令来对其进行控制和调试。我认为一个你无法完全掌控的个体才更能让你感觉到它的自主性，而视其成为一个对话机器。
> - SengokuCola~~纯编程外行，面向 cursor 编程，很多代码写得不好多多包涵~~已得到大脑升级。

## 🙋 贡献和致谢
你可以阅读[开发文档](https://docs.mai-mai.org/develop/)来更好的了解麦麦!  
MaiCore 是一个开源项目，我们非常欢迎你的参与。你的贡献，无论是提交 bug 报告、功能需求还是代码 pr，都对项目非常宝贵。我们非常感谢你的支持！🎉  
但无序的讨论会降低沟通效率，进而影响问题的解决速度，因此在提交任何贡献前，请务必先阅读本项目的[贡献指南](docs-src/CONTRIBUTE.md)。(待补完)  

### 贡献者

感谢各位大佬！  

<a href="https://github.com/MaiM-with-u/MaiBot/graphs/contributors">
  <img alt="contributors" src="https://contrib.rocks/image?repo=MaiM-with-u/MaiBot" />
</a>

### 致谢

- [略nd](https://space.bilibili.com/1344099355): 为麦麦绘制人设。
- [NapCat](https://github.com/NapNeko/NapCatQQ): 现代化的基于 NTQQ 的 Bot 协议端实现。

**也感谢每一位给麦麦发展提出宝贵意见与建议的用户，感谢陪伴麦麦走到现在的你们！**

## 📌 注意事项

> [!WARNING]
> 使用本项目前必须阅读和同意[用户协议](EULA.md)和[隐私协议](PRIVACY.md)。  
> 本应用生成内容来自人工智能模型，由 AI 生成，请仔细甄别，请勿用于违反法律的用途，AI 生成内容不代表本项目团队的观点和立场。

## 麦麦仓库状态

![Alt](https://repobeats.axiom.co/api/embed/9faca9fccfc467931b87dd357b60c6362b5cfae0.svg "麦麦仓库状态")

### Star 趋势

[![Star 趋势](https://starchart.cc/MaiM-with-u/MaiBot.svg?variant=adaptive)](https://starchart.cc/MaiM-with-u/MaiBot)

## License

GPL-3.0
