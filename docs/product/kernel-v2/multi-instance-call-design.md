# 多实例子流程设计稿（A9，v2.3 第四批）

| | |
|---|---|
| 状态 | **已实施（v2.3 第十三轮）**——契约三件套+失败语义+validator 全落地，见 spec 附录 B 第十三轮 |
| 依据 | [对标明细详设.md A9](./对标明细详设.md)（Zeebe 契约定稿）+ [delta-industry-benchmark.md §9](./delta-industry-benchmark.md) |

## 字段与语义（Zeebe inputCollection/outputCollection 契约）

`CallActivitySpec.multi_instance: CallInstanceConfig | None`（新文件 definitions/call_instance.py，守 builtin.py 行数红线）：

```python
class CallInstanceConfig(BaseModel):
    subjects: list[str]        # 取人表达式（CEL，复用 resolver：查无传播/上限护栏）
    completion: str = "all"    # "all" | CEL（注入 child_total/child_completed/child_failed）
    output_mode: str = "collect"  # collect=index 对齐聚合 list / merge=逐子覆写（仅单子）
```

- **fan-out**：subjects 逐个解析（取人语义），每人一个子实例；**输入映射按子求值**
  且注入保留标识符 `subject`（catalog 入册，位点 mapping_input）——
  `input_mappings={"dept": "subject"}` 即每子拿自己的切片。
- **index 对齐**：子实例行新增 `parent_index` 列（迁移 0003）——第 k 个 subject 的
  子实例输出落聚合列表第 k 位；**未写 → `null` 占位**（下标稳定，并行完成序无关）；
  **空集合 → 节点立即完成**（等价 skip，显式成功不报错——Zeebe 同款）。
- **completion 只做「提前收工」不做失败机制**（C7 教训：提前完成丢失「谁反对」）：
  公式命中时终止剩余 running 子实例（直接置 cancelled+留事件，v1 不嵌套命令）；
  suspended 子不终止（管理员域）。缺省 `"all"`＝全员结算（completed+failed+terminated
  ==total）。
- **失败语义**：suspended/paused 子阻塞父（等待批已修：结算=终态四态）；提前收工的
  失败计数 `child_failed`∈{terminated,cancelled,failed} 供公式表达「任一失败即整体
  失败」（`child_failed > 0` 阻断）或「忽略失败」（`child_completed == child_total`）。
- **merge 仅限单子**（validator ERROR 拦 MI+merge——多子逐子覆写=last-wins 顺序
  不可控，正是 Zeebe 之前的 C7 公开痛点）。

## 目录与位点

catalog 新增：`subject`（type=subject，位点 mapping_input）；`child_total/child_completed/
child_failed`（type=int，新位点 completion——iter_expression_positions 对 MI completion
公式按该 kind 产出，静态检查表驱动接入）。

## 不做（本轮边界）

MI 内动态加实例（Zeebe 也不支持 add）；串行模式（N 个串联 call_activity 更直白）；
行级触发 isSheetTrigger（等 business_object/M-B）。

**验收**：三人三子全完（collect 对齐+null 占位）/ 空集即完 / 提前收工终止剩余 /
suspended 阻塞 / child_failed 公式两形态 / merge+MI 被拦 / 单子回归不变 / 模拟试跑
递归（simulated 子实例自动完成）。
