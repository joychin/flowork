"""内置节点类型的设计器展示元数据（`ui=` 载荷）。

节点**类型定义**留在 builtin.py，**展示元数据**（显示名/调色板
分组/画布形状/属性面板分组/字段标签）集中到此——纯数据、零依赖，设计器
（`designer/` 读 `/api/meta`）与注册入口 `register_builtin_nodes` 共用一份。

字段说明（与 registry.register 的 ui 契约一致）：
- name 显示名 / category 调色板分组 / shape 画布形状 / color 主色 / icon 图标；
- palette 调色板预设项（含创建节点时的字段默认值）；
- singleton 单例标记：画布上这类节点只保留一枚——已有时添加入口置灰、最后一枚
  不可删。start 是校验硬要求（structure.start.count＝恰好一个）；end 是产品口径
  的默认一枚（引擎本身允许 ≥1 个终点，JSON/AI 写路径仍可表达多终点）；
- form_groups 属性面板分组（title + fields，collapsed 默认折叠=渐进披露）；
- field_labels 路径标签：设计器表单按点路径解析，用于同名字段在不同 kind 下
  含义不同的场景（如 multi_instance 在 user_task 是会签配置、在 call_activity
  是扇出配置）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["BUILTIN_UI"]

BUILTIN_UI: dict[str, dict[str, Any]] = {
    "start": {
        "name": "开始",
        "category": "事件",
        "shape": "circle",
        "color": "#16a34a",
        "icon": "play",
        "singleton": True,
        "palette": [{"label": "开始", "defaults": {}}],
        "form_groups": [{"title": "基础", "fields": ["name"]}],
    },
    "end": {
        "name": "结束",
        "category": "事件",
        "shape": "circle",
        "color": "#dc2626",
        "icon": "stop",
        "singleton": True,
        "palette": [{"label": "结束", "defaults": {}}],
        "form_groups": [{"title": "基础", "fields": ["name"]}],
    },
    "branch": {
        "name": "条件分支",
        "category": "分支",
        "shape": "rect",
        "color": "#d97706",
        "icon": "branch",
        "palette": [
            {"label": "条件分支", "defaults": {"route_mode": "first_match"}},
            {"label": "条件分支(命中的都走)", "defaults": {"route_mode": "all_match"}},
        ],
        "form_groups": [{"title": "基础", "fields": ["name", "route_mode"]}],
    },
    "user_task": {
        "name": "审批任务",
        "category": "任务",
        "shape": "rect",
        "color": "#2563eb",
        "icon": "user",
        "palette": [
            {
                "label": "审批任务",
                "defaults": {
                    "assignee": None,
                    "multi_instance": {"mode": "parallel"},
                    "timeout": {"due_minutes": 1440, "strategy": "notify"},
                },
            }
        ],
        # 属性面板分组：常用在前，高级组默认折叠（渐进披露）
        "form_groups": [
            {"title": "基础", "fields": ["name"]},
            {
                "title": "参与者",
                "fields": [
                    "assignee",
                    "assignees",
                    "candidate_users",
                    "candidate_groups",
                    "no_participant_policy",
                ],
            },
            {"title": "多人审批", "fields": ["multi_instance"], "collapsed": True},
            {
                "title": "自动通过",
                "fields": [
                    "auto_pass_originator",
                    "auto_pass_previous",
                    "auto_pass_participated",
                ],
                "collapsed": True,
            },
            {
                "title": "驳回与超时",
                "fields": ["reject_target", "deadlines", "timeout"],
                "collapsed": True,
            },
        ],
        # 路径标签(设计器表单按点路径解析;同名字段在别的 kind 下含义不同时由此区分)
        "field_labels": {
            "multi_instance.approve_exit": "同意出口(CEL 公式,缺省=全员同意)",
            "multi_instance.disapprove_exit": "驳回出口(CEL 公式,缺省=全员否决)",
            "deadlines[].after_minutes": "进入节点后多少分钟",
            "deadlines[].action": "到期动作",
            "deadlines[].reassign_to": "改派目标(CEL,仅 reassign 需要)",
            "deadlines[].escalate_to": "升级目标(CEL,仅 escalate,如 manager_of(task.assignee))",
            "timeout.due_minutes": "超时时间(分钟)",
            "timeout.strategy": "到期动作",
        },
    },
    "service_task": {
        "name": "服务任务",
        "category": "自动化",
        "shape": "rect",
        "color": "#7c3aed",
        "icon": "settings",
        "palette": [{"label": "服务任务", "defaults": {"action": ""}}],
        "form_groups": [
            {"title": "基础", "fields": ["name"]},
            {"title": "动作", "fields": ["action", "params"]},
        ],
    },
    "call_activity": {
        "name": "子流程",
        "category": "自动化",
        "shape": "rect",
        "color": "#475569",
        "icon": "box",
        "palette": [{"label": "子流程", "defaults": {"process_code": ""}}],
        "form_groups": [
            {"title": "基础", "fields": ["name"]},
            {"title": "子流程", "fields": ["process_code", "binding", "is_synchronous"]},
            {
                "title": "多实例(按主体展开)",
                "fields": ["multi_instance"],
                "collapsed": True,
            },
            {
                "title": "数据映射",
                "fields": ["input_mappings", "output_mappings"],
                "collapsed": True,
            },
        ],
        "field_labels": {
            "binding": "绑定方式",
            "multi_instance": "多实例子流程",
            "multi_instance.subjects": "参与主体(每项一个 CEL 表达式=一个子实例)",
            "multi_instance.completion": (
                "完成公式(child_total/completed/failed 三计数;缺省 all=全员结算)"
            ),
            "multi_instance.output_mode": "输出聚合方式",
        },
    },
    "cc_task": {
        "name": "传阅",
        "category": "任务",
        "shape": "rect",
        "color": "#0d9488",
        "icon": "mail",
        "palette": [{"label": "传阅", "defaults": {"recipients": []}}],
        "form_groups": [
            {"title": "基础", "fields": ["name"]},
            {"title": "接收", "fields": ["recipients"]},
        ],
    },
}
