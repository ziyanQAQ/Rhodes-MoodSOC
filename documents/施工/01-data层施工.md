# data 层施工

## 1. 目标与边界

data 层把上游静态资料装配成版本化、只读、可索引的目录。它不计算心情、不决定候选、不保存某次模拟的实时布局，也不依赖 UI、Worker、HTTP 或存储实现。

事实来源优先级：`Kengxxiao/ArknightsGameData/zh_CN/gamedata/excel/` → 当前项目经过审阅的规范化记录 → 明确标记为待核实的占位。任何事实缺口都必须停在数据核验，不得由实现者猜测。

## 2. 推荐 Module 与 Interface

建议文件：

```text
src/data/
  catalog-types.ts
  raw-loader.ts
  normalizer.ts
  validator.ts
  catalog-builder.ts
```

`CatalogReader` Interface 由 [`03-application层施工.md`](03-application层施工.md) 的 `application/ports.ts` 定义，并由 infrastructure 的目录 Adapter 实现。data 只产出不可变记录和快照，不拥有外部读取端口。规划的数据结构：

```ts
type CatalogVersion = { id: string; source: string; generatedAt: string };
interface CatalogSnapshot {
  readonly version: CatalogVersion;
  readonly operators: ReadonlyMap<OperatorId, OperatorRecord>;
  readonly skills: ReadonlyMap<SkillId, SkillRecord>;
  readonly facilityTypes: ReadonlyMap<FacilityTypeId, FacilityTypeRecord>;
  readonly factions: ReadonlyMap<FactionId, FactionRecord>;
}
```

快照提供统一索引、版本检查和只读保证，不暴露上游 JSON 结构。infrastructure 的 `CatalogReader` Adapter 读取并校验来源，再调用 data builder 形成快照；application 在模拟开始时选取所需数据并冻结快照传给 domain。domain 不得直接打开文件、解析上游 JSON 或依赖 `CatalogReader`。

## 3. 数据对象与不变量

- `OperatorRecord`：稳定 `operatorId`、目录显示信息、阵营 ID、可用技能 ID；姓名仅为展示字段。
- `SkillRecord`：稳定 `skillId`、来源字段、分类、模板参数、适用条件；不能把干员名称特判写在规则引擎。
- `FacilityTypeRecord`：设施类型、容量模型、位次模型、可用规则引用。
- `FactionRecord`：稳定阵营 ID 和成员 ID 列表；成员关系必须能回溯来源。
- 所有数值明确单位、精度和来源；涉及模拟的十进制数采用字符串或定点表示。
- 目录内 ID 唯一；引用必须存在；未知字段可保留在原始层但不能未经审阅进入领域层。
- 目录构建完成后深冻结或使用不可变结构；运行时不能修改。

## 4. 施工步骤

1. 登记上游仓库 commit、数据目录和读取日期。
2. 将原始文件映射到内部 raw 类型，不在 loader 中写业务规则。
3. normalizer 统一 ID、枚举、单位和十进制格式。
4. validator 检查重复 ID、缺失引用、非法数值、未知版本和待核实字段。
5. builder 生成索引并输出 `CatalogVersion` 与生成元数据。
6. infrastructure Adapter 使用 data builder 形成只读 `CatalogSnapshot`，并实现 application 定义的 `CatalogReader`；application 将快照交给 domain。domain 不能看到 raw 对象或目录 Adapter。

## 5. 错误与兼容

目录缺失、版本不匹配、关键字段缺失、引用不存在应分别产生稳定错误码，例如 `CATALOG_NOT_FOUND`、`CATALOG_VERSION_UNSUPPORTED`、`CATALOG_FIELD_INVALID`、`CATALOG_REFERENCE_MISSING`。不能静默使用零值或显示名兜底。新增可选字段允许向后兼容；删除或改变语义必须提升目录版本。

## 6. 扩展方式

新增干员/技能/设施优先添加目录记录、模板参数或策略引用。只有行为组合确实无法由现有模板表达时，才增加新的策略类型，并为其增加黑盒测试。禁止在通用模拟循环中追加名称分支。

## 7. 黑盒验收

实现阶段必须先写测试验证：同一原始数据生成相同版本和索引；重复/缺失引用被拒绝；未知技能不会静默变成无技能；目录对象不可被调用方修改；上游字段变更能给出明确错误。测试通过后才更新数据文档和版本记录。

## 8. 当前实现状态

已实现第一条垂直切片：`src/data/catalog-types.ts`、`src/data/catalog-builder.ts` 和 `src/data/index.ts` 提供目录定义、版本元数据、稳定 ID 查询、重复 ID/悬空引用校验和冻结结果。真实上游文件加载、规范化生成器和全量目录仍待实现。
