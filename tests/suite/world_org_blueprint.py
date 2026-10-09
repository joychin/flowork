"""真实蓝图组织增量：金赛药业 + 人力资源服务公司（BPO）两棵树。

来源文档（docs_research 不入库，角色/部门照文档人设虚拟化）：
- 金赛《BPM_10 系统物料主数据-产成品研发产品设计说明 V1.5》：采购部申请人 +
  MRP/质量QA/质量QC/仓库/销售/财务六个视图归口人 + 主数据管理组。
- BPO《任务发布 BS-PRJ-004 / 人员合同签订 BS-FILES-002 / 收款到账 / 付款申请》：
  项目部（项目经理/项目部领导）、保险部、结算部、劳动关系协调部、办公室、
  财务部（出纳/财务总监）、企业服务中心（档案专员/主任/办卡专员）、销售组、高管。
拼接到 world_org 主清单（store/static 两种物化自动获得；孤儿自检随之覆盖）。
"""

from __future__ import annotations

# —— 金赛药业树：(code, 名称, 上级, 部门主管) ——
ORG_UNITS_BLUEPRINT: list[tuple[str, str, str | None, str | None]] = [
    ("jin_hq", "金赛药业", None, "jin_mdm"),
    ("jin_pur", "采购部", "jin_hq", "jin_buyer"),
    ("jin_pp", "生产计划部", "jin_hq", "jin_planner"),
    ("jin_qa", "质量部", "jin_hq", "jin_qa"),
    ("jin_wh", "仓储部", "jin_hq", "jin_keeper"),
    ("jin_sales", "销售部", "jin_hq", "jin_sales_rep"),
    ("jin_fin", "财务部", "jin_hq", "jin_fin_clerk"),
    # —— BPO（人力资源服务公司）树 ——
    ("bpo_hq", "人力服务公司", None, "bpo_chairman"),
    ("bpo_prj", "项目部", "bpo_hq", "bpo_prj_head"),
    ("bpo_ins", "保险部", "bpo_hq", "bpo_ins_head"),
    ("bpo_set", "结算部", "bpo_hq", "bpo_set_head"),
    ("bpo_med", "劳动关系协调部", "bpo_hq", "bpo_med_head"),
    ("bpo_off", "办公室", "bpo_hq", "bpo_off_head"),
    ("bpo_fin", "财务部", "bpo_hq", "bpo_fin_director"),
    ("bpo_srv", "企业服务中心", "bpo_hq", "bpo_srv_head"),
    ("bpo_sales", "销售组", "bpo_hq", "bpo_tl"),
]

# —— 用户：(id, 姓名, 主属部门) ——
USERS_BLUEPRINT: list[tuple[str, str, str]] = [
    # 金赛：申请人 + 六视图归口 + 主数据管理员
    ("jin_buyer", "金采购", "jin_pur"),
    ("jin_planner", "周计划", "jin_pp"),
    ("jin_qa", "陈质量", "jin_qa"),
    ("jin_qc", "刘检验", "jin_qa"),
    ("jin_keeper", "库管员", "jin_wh"),
    ("jin_sales_rep", "金销售", "jin_sales"),
    ("jin_fin_clerk", "钱财务", "jin_fin"),
    ("jin_mdm", "孙主数据", "jin_hq"),
    # BPO
    ("bpo_chairman", "董事长", "bpo_hq"),
    ("bpo_gm", "总经理", "bpo_hq"),
    ("bpo_pm", "邬项目经理", "bpo_prj"),
    ("bpo_prj_head", "段主任", "bpo_prj"),
    ("bpo_set_sp", "结算专员", "bpo_set"),
    ("bpo_set_head", "结算部长", "bpo_set"),
    ("bpo_ins_sp", "保险专员", "bpo_ins"),
    ("bpo_ins_head", "保险部长", "bpo_ins"),
    ("bpo_med_sp", "调解专员", "bpo_med"),
    ("bpo_med_head", "调解部长", "bpo_med"),
    ("bpo_office", "办公室职员", "bpo_off"),
    ("bpo_off_head", "办公室主任", "bpo_off"),
    ("bpo_cashier", "田出纳", "bpo_fin"),
    ("bpo_fin_director", "财务总监", "bpo_fin"),
    ("bpo_archivist", "档案专员", "bpo_srv"),
    ("bpo_srv_head", "企服主任", "bpo_srv"),
    ("bpo_card_clerk", "办卡专员", "bpo_srv"),
    ("bpo_am", "客户经理", "bpo_sales"),
    ("bpo_tl", "销售组长", "bpo_sales"),
]

# —— 角色：(code, 名称, 默认范围) ——
ROLES_BLUEPRINT: list[tuple[str, str, str | None]] = [
    ("mrp_owner", "MRP视图归口人", None),
    ("qa_owner", "QA视图归口人", None),
    ("qc_owner", "QC视图归口人", None),
    ("wh_owner", "仓库视图归口人", None),
    ("sales_view_owner", "销售视图归口人", None),
    ("fin_view_owner", "财务视图归口人", None),
    ("mdm_admin", "主数据管理组", None),
    ("project_manager", "项目经理", None),
    ("prj_head", "项目部领导", None),
    ("settle_specialist", "结算专员", None),
    ("settle_head", "结算部部长", None),
    ("insurance_specialist", "保险专员", None),
    ("insurance_head", "保险部部长", None),
    ("mediate_specialist", "调解专员", None),
    ("mediate_head", "劳动关系协调部部长", None),
    ("office_staff", "办公室人员", None),
    ("office_head", "办公室主任", None),
    ("cashier", "出纳", None),
    ("finance_director", "财务总监", None),
    ("chairman", "董事长", None),
    ("general_manager", "总经理", None),
    ("account_manager", "客户经理", None),
    ("team_leader", "销售组长", None),
    ("archivist", "档案专员", None),
    ("service_center_head", "企业服务中心主任", None),
    ("card_clerk", "办卡专员", None),
]

ROLE_ASSIGNMENTS_BLUEPRINT: list[tuple[str, str, str | None]] = [
    ("jin_planner", "mrp_owner", None),
    ("jin_qa", "qa_owner", None),
    ("jin_qc", "qc_owner", None),
    ("jin_keeper", "wh_owner", None),
    ("jin_sales_rep", "sales_view_owner", None),
    ("jin_fin_clerk", "fin_view_owner", None),
    ("jin_mdm", "mdm_admin", None),
    ("bpo_pm", "project_manager", None),
    ("bpo_prj_head", "prj_head", None),
    ("bpo_set_sp", "settle_specialist", None),
    ("bpo_set_head", "settle_head", None),
    ("bpo_ins_sp", "insurance_specialist", None),
    ("bpo_ins_head", "insurance_head", None),
    ("bpo_med_sp", "mediate_specialist", None),
    ("bpo_med_head", "mediate_head", None),
    ("bpo_office", "office_staff", None),
    ("bpo_off_head", "office_head", None),
    ("bpo_cashier", "cashier", None),
    ("bpo_fin_director", "finance_director", None),
    ("bpo_chairman", "chairman", None),
    ("bpo_gm", "general_manager", None),
    ("bpo_am", "account_manager", None),
    ("bpo_tl", "team_leader", None),
    ("bpo_archivist", "archivist", None),
    ("bpo_srv_head", "service_center_head", None),
    ("bpo_card_clerk", "card_clerk", None),
]
