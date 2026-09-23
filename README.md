# Ws-Web-relic-data

Warframe 遗物（核桃）相关数据的自动收集仓库，供 Ws-Web-relic 站点用。

## 里面有什么

- `data/relics.json`：遗物掉落表（按基础名分组，四个精炼等级合并）
- `data/reward-items.json`：遗物奖励物品（去重，含稀有度）
- `data/prices.json`：奖励物品 72 小时滚动价格（含均价）
- `data/prices-summary.json`：价格摘要（给页面用的精简版）
- `data/relic-deep-date.json` / `relic-deep-date-summary.json`：遗物状态变化（入库/出库）的天级历史
- `data/item-categories.json`：物品分类
- `data/item-names-zh.json`：物品中文名
- `data/update-versions.json`：版本号对照

## 更新方式

`.github/workflows/` 里的定时任务自动刷新这些数据，不用手动管。

## 说明

- 遗物键名及历史元数据来自 WFCD；普通遗物的奖励、稀有度和四档概率统一使用 `Ws-Web-assets/data/item/drops-index.json` 中的官方掉落表，并补齐 WFCD 尚未收录的新遗物。官方奖励须满足每档概率合计约 100%、3 铜 2 银 1 金及完整交易映射，否则停止发布。安魂遗物沿用 WFCD 独立规则。
- `relic-deep-date.json` 的奖励与 `relics.json` 一致；wiki 只提供历史版本和入库状态。wiki 尚未收录的普通遗物也进入深度数据，日期留空。最近两个版本索引每次刷新；没有对应版本日期时不借用大版本日期。
- 北京与美东日期只有在来源提供 UTC 时间戳时才双向换算；只有单个时区日期时，另一栏显示“未确定日期”。patchlogs 的时间戳代表日志发布时间，不能证明游戏事件发生的秒数。
- 奖励物品的交易链接和中英名称取自 `Ws-Web-assets/data/item/wm-items.json`；WM 大量订单接口失败时保留上次价格产物，不发布被清空的价格库。
- 每小时更新遗物及价格后，自动触发生命周期数据刷新，供 `relic-vault` 搜索新遗物
- 非官方项目，和 Digital Extremes 没关系
