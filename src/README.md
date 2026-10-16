# 源码框架

源码按 [`documents/施工/00-施工总览.md`](../documents/施工/00-施工总览.md) 的依赖顺序组织。当前实现 data 目录校验/快照/生成格式、infrastructure/catalog 文件 Adapter，以及 domain 的数值、时间线、名册与布局基础；其他目录是明确标注为待实现的边界，不提供空壳业务函数。

```text
data → domain → application → infrastructure → presentation
```

domain 不得依赖 application、infrastructure 或 presentation；application 定义 ports；infrastructure 实现 ports；presentation 只调用 application 用例。
