"""占用档（预约/暂存）尚未上线 —— 空桩阶段只保留资格锁。

真实占用落地前，本模块锁死三件事，由测例守卫：
1. 无主/脏数据物品不进可占用集合（档口未开，occupiable 恒 False，干净物品同样不可占用）；
2. 未来的占用确认不得把 items.data_quality 洗成 clean、不得补造 owner；
3. 占用与直接借出不得双活：当前无占用表、无占用入口，库内只有 loans 一个活口。

档口落地后用真实的占用表互斥核对替换本桩，对应测试同步改写，不得直接删除。
"""

OCCUPANCY_UNAVAILABLE = True

def occupiable(item: dict) -> bool:
    if OCCUPANCY_UNAVAILABLE:
        return False
    # 落地后至少与可借资格链同源：被可借链拒绝的无主脏数据同样不得占用。
    from app.engines.borrow_rules import shows_as_available
    return shows_as_available(item)
