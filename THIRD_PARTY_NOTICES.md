# 来源与第三方组件

浏览器版迁移自 [AneryCoft/course_helper](https://github.com/AneryCoft/course_helper)，保留根目录的 GPL v3 [LICENSE](LICENSE)。原项目致谢和声明见 [原 README](course_helper/README.md)。

浏览器 IM 使用 `easemob-websdk` 4.24.0，发布包的许可元数据为 ISC。源码入口是 `app/static/im-source.js`，打包文件是 `app/static/im.js`；固定版本见 `package-lock.json`。构建器 esbuild 0.25.x 使用 MIT 许可，只用于构建。构建文件保留上游提供的许可注释。

Python 依赖版本见 `uv.lock`，运行和开发依赖见 `pyproject.toml`。这些组件保留各自发布包中的版权和许可，项目的 GPL 许可不改变第三方组件自身的许可。

学习通作业列表和已提交记录解析来自官方页面结构，协议查证来源记录在 [开发文档](docs/DEVELOPMENT.md)。未复制第三方题库、AI 答题服务或商业脚本实现。
