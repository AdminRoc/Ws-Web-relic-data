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

- 遗物定义优先读取 WFCD，并用 `Ws-Web-assets/data/item/drops-index.json` 中的官方掉落表补齐 WFCD 尚未收录的新遗物；奖励物品的交易链接和中英名称取自同仓 `wm-items.json`
- 每小时更新遗物及价格后，自动触发生命周期数据刷新，供 `relic-vault` 搜索新遗物
- 非官方项目，和 Digital Extremes 没关系
