#!/usr/bin/env python3
"""Phase 12 候选 Query 生成与校验入口。

AI 可以生成候选问题，但不能替代人工 Gold Set 确认。默认输出
``eval/fixtures/phase12_candidates.json``，全部条目保持 ``pending``；
``--mode formal`` 只接受全部为 ``confirmed`` 的数据集，并拒绝写回。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.dataset import load_dataset
from app.evaluation.lexical_experiment import retrieve_chunks
from app.evaluation.metrics import normalize_text
from app.evaluation.phase12_dataset import (
    CATEGORY_COUNTS,
    Phase12Category,
    Phase12Dataset,
    Phase12DatasetError,
    category_counts,
    load_phase12_dataset,
    validate_phase12_dataset,
)

DEFAULT_DATASET = Path("eval/fixtures/phase12_candidates.json")
DEFAULT_CORPUS = Path("eval/fixtures/phase6_simulated.json")


def _query(
    query_id: str,
    category: Phase12Category,
    text: str,
    intent: str,
    *,
    source_type: str,
    source_id: str,
    chunk_index: int,
    evidence: str,
    answer_points: list[str],
    filters: dict[str, str | list[str]] | None = None,
) -> dict[str, Any]:
    gold_document = {
        "source_type": source_type,
        "source_id": source_id,
        "evidence": evidence,
    }
    return {
        "query_id": query_id,
        "query": text,
        "category": category.value,
        "expected_intent": intent,
        "expected_filters": filters or {},
        "gold_documents": [gold_document],
        "gold_chunks": [
            {
                "source_type": source_type,
                "source_id": source_id,
                "chunk_index": chunk_index,
                "evidence": evidence,
            }
        ],
        "expected_answer_points": answer_points,
        "answerable": True,
        "generated_by": "ai-candidate-v1",
        "review_status": "pending",
    }


def _unanswerable(
    query_id: str,
    text: str,
    intent: str,
) -> dict[str, Any]:
    return {
        "query_id": query_id,
        "query": text,
        "category": Phase12Category.UNANSWERABLE.value,
        "expected_intent": intent,
        "expected_filters": {},
        "gold_documents": [],
        "gold_chunks": [],
        "expected_answer_points": [],
        "answerable": False,
        "generated_by": "ai-candidate-v1",
        "review_status": "pending",
    }


def _candidate_queries() -> list[dict[str, Any]]:
    """确定性候选集：每类数量与计划一致，全部等待人工审阅。"""
    queries: list[dict[str, Any]] = []

    testcase_rows = [
        (
            "SHOP-LOGIN-001",
            "登录成功后系统会怎样创建会话？",
            "服务端创建新的会话",
            "确认成功登录会创建新会话",
        ),
        (
            "SHOP-LOGIN-002",
            "连续输错五次密码后账户会怎样？",
            "账户锁定三十分钟",
            "确认连续失败触发三十分钟锁定",
        ),
        (
            "SHOP-LOGIN-003",
            "登录跳转地址里能看到访问令牌吗？",
            "地址栏不出现访问令牌",
            "确认令牌不出现在 URL",
        ),
        (
            "SHOP-CATALOG-001",
            "商品搜索翻到第二页后关键词还在吗？",
            "分页控件保留当前关键词",
            "确认分页保留搜索关键词",
        ),
        (
            "SHOP-CATALOG-002",
            "颜色和尺寸筛选刷新后还会保留吗？",
            "刷新后两个筛选条件仍被保留",
            "确认筛选条件刷新后保留",
        ),
        (
            "SHOP-CART-001",
            "购物车数量超过库存时还能结算吗？",
            "不能进入支付页",
            "确认超库存时阻止结算",
        ),
        (
            "SHOP-CHECKOUT-001",
            "订单满三百元时标准运费是多少？",
            "运费显示为零",
            "确认满额标准配送免运费",
        ),
        (
            "SHOP-CHECKOUT-002",
            "返回确认页再提交会生成几个订单？",
            "只产生一个待支付订单",
            "确认返回重提不会重复下单",
        ),
        (
            "SHOP-PAYMENT-001",
            "信用卡支付成功后订单状态会变化吗？",
            "订单由待支付变为已支付",
            "确认支付成功后更新订单状态",
        ),
        (
            "SHOP-PAYMENT-002",
            "支付超时后再次支付会造成重复扣款吗？",
            "同一幂等键只对应一笔成功扣款",
            "确认超时重试不产生双扣",
        ),
        (
            "SHOP-PAYMENT-003",
            "连续应用两个优惠码会叠加折扣吗？",
            "只接受一个符合规则的优惠码",
            "确认优惠码不能叠加",
        ),
        (
            "SHOP-REFUND-001",
            "部分退款后订单和账务金额如何保持一致？",
            "账务流水与订单金额完全一致",
            "确认部分退款后的金额一致性",
        ),
        (
            "SHOP-LOGIN-001",
            "成功登录后页面会显示哪个账户？",
            "页面右上角显示 alice@example.test",
            "确认登录后的账户展示",
        ),
        (
            "SHOP-LOGIN-002",
            "账户锁定期间使用正确密码能登录吗？",
            "锁定期间即使密码正确也拒绝登录",
            "确认锁定期间拒绝登录",
        ),
        (
            "SHOP-LOGIN-003",
            "会话标识应该保存在哪里？",
            "会话标识仅存在于安全 Cookie 中",
            "确认会话标识的存储位置",
        ),
        (
            "SHOP-CATALOG-001",
            "商品搜索结果翻页时会不会重复或遗漏？",
            "不重复也不遗漏",
            "确认分页结果的完整性",
        ),
        (
            "SHOP-CATALOG-002",
            "同时选择蓝色和中号会返回哪些商品？",
            "只显示同时满足两个条件的商品",
            "确认多条件筛选的交集语义",
        ),
        (
            "SHOP-CART-001",
            "购物车数量改到库存上限以上会怎样？",
            "页面提示库存不足",
            "确认超库存时的用户提示",
        ),
        (
            "SHOP-CHECKOUT-001",
            "订单总价里会出现隐藏服务费吗？",
            "不出现隐藏服务费",
            "确认满额订单没有隐藏费用",
        ),
        (
            "SHOP-CHECKOUT-002",
            "重复提交订单会被什么机制拦截？",
            "重复提交必须被幂等键拦截",
            "确认订单提交的幂等保护",
        ),
        (
            "SHOP-PAYMENT-001",
            "支付成功后如何追溯交易？",
            "记录可追溯的交易号",
            "确认支付成功记录交易号",
        ),
        (
            "SHOP-PAYMENT-002",
            "支付超时后用户应该看到什么状态？",
            "客户看到明确处理中或失败状态",
            "确认超时后的用户可见状态",
        ),
        (
            "SHOP-PAYMENT-003",
            "快速重复点击优惠码按钮会累计折扣吗？",
            "并发重复提交不会让折扣累计两次",
            "确认并发点击不重复累计",
        ),
        (
            "SHOP-REFUND-001",
            "退款二十元后订单剩余金额是多少？",
            "剩余一百元",
            "确认部分退款后的剩余金额",
        ),
        (
            "SHOP-LOGIN-001",
            "登录测试需要使用什么账户？",
            "账户 alice@example.test 已激活",
            "确认登录用例的前置账户",
        ),
        (
            "SHOP-CATALOG-001",
            "搜索旅行商品需要满足什么数据前置条件？",
            "商品库至少存在三十件名称含“旅行”的商品",
            "确认搜索分页测试的数据前置",
        ),
        (
            "SHOP-CART-001",
            "库存不足时允许先下单后人工取消吗？",
            "不能进入支付页",
            "确认库存不足时禁止创建订单",
        ),
        (
            "SHOP-CHECKOUT-001",
            "标准配送免运测试的前置规则是什么？",
            "标准运费规则为满三百元免运费",
            "确认标准配送免运测试的前置规则",
        ),
        (
            "SHOP-PAYMENT-002",
            "查询网关流水时应该看到几笔成功扣款？",
            "同一幂等键只对应一笔成功扣款",
            "确认网关流水只有一笔成功扣款",
        ),
        (
            "SHOP-REFUND-001",
            "部分退款后订单详情会显示哪些金额？",
            "订单显示已退款二十元",
            "确认订单详情展示退款金额",
        ),
    ]
    for index, (source_id, text, evidence, answer_point) in enumerate(testcase_rows, start=1):
        queries.append(
            _query(
                f"P12-TC-{index:03d}",
                Phase12Category.TESTCASE_SEARCH,
                text,
                "定位与问题最相关的测试用例及预期结果",
                source_type="testcase",
                source_id=source_id,
                chunk_index=0,
                evidence=evidence,
                answer_points=[answer_point],
            )
        )

    defect_rows = [
        (
            "SHOP-BUG-001",
            "支付超时重试为什么会造成重复扣款？",
            "客户端每次提交都生成新幂等键",
            "定位重复扣款的根因",
        ),
        (
            "SHOP-BUG-002",
            "返回确认页后为什么会出现两个待支付订单？",
            "缺少客户端订单草稿标识",
            "定位重复订单的根因",
        ),
        (
            "SHOP-BUG-003",
            "优惠码连点后为什么折扣会累计两次？",
            "没有事务锁",
            "定位优惠重复累计的根因",
        ),
        (
            "SHOP-BUG-004",
            "登录失败提示为什么会泄露邮箱是否注册？",
            "未使用统一的安全提示文案",
            "定位账户枚举风险的根因",
        ),
        (
            "SHOP-BUG-005",
            "部分退款后订单总额为什么没有扣减？",
            "没有更新订单的净额投影",
            "定位退款金额不同步的根因",
        ),
        (
            "SHOP-BUG-001",
            "重复扣款缺陷的复现步骤是什么？",
            "返回结算页后再次支付同一订单",
            "复现支付超时后的重复扣款",
        ),
        (
            "SHOP-BUG-002",
            "重复订单缺陷的解决方案是什么？",
            "确认页按草稿标识复用",
            "确认重复订单的修复方案",
        ),
        (
            "SHOP-BUG-003",
            "优惠码并发缺陷应该怎样修复？",
            "对订单折扣行使用行锁",
            "确认优惠码并发缺陷的修复方案",
        ),
        (
            "SHOP-BUG-004",
            "账户锁定提示缺陷应该怎样修复？",
            "所有认证失败统一返回通用提示",
            "确认账户枚举缺陷的修复方案",
        ),
        (
            "SHOP-BUG-005",
            "退款总额缺陷应该怎样修复？",
            "退款事务内发布净额变更事件",
            "确认退款金额同步的修复方案",
        ),
        (
            "SHOP-BUG-001",
            "支付重复扣款缺陷修复后要求怎样重试？",
            "超时后查询原交易状态而不是盲目重试",
            "定位重复扣款缺陷的修复要求",
        ),
        (
            "SHOP-BUG-002",
            "重复订单缺陷修复时如何复用草稿订单？",
            "确认页按草稿标识复用",
            "定位重复订单缺陷的修复方式",
        ),
        (
            "SHOP-BUG-003",
            "优惠码缺陷修复时如何保证事务一致性？",
            "对订单折扣行使用行锁",
            "定位优惠码缺陷的事务修复",
        ),
        (
            "SHOP-BUG-004",
            "账户枚举缺陷修复后失败提示怎样统一？",
            "所有认证失败统一返回通用提示",
            "定位账户枚举缺陷的提示修复",
        ),
        (
            "SHOP-BUG-005",
            "退款金额缺陷修复后如何更新读模型？",
            "读模型按事件重算",
            "定位退款金额缺陷的读模型修复",
        ),
        (
            "SHOP-BUG-001",
            "支付重复扣款缺陷涉及哪个订单保护约束？",
            "按订单号加锁",
            "定位重复扣款缺陷的数据约束",
        ),
        (
            "SHOP-BUG-002",
            "重复订单缺陷缺少什么客户端标识？",
            "缺少客户端订单草稿标识",
            "定位重复订单缺陷缺失的标识",
        ),
        (
            "SHOP-BUG-003",
            "优惠码缺陷中两个请求基于什么状态计算？",
            "两个请求都基于旧状态计算",
            "定位优惠码并发缺陷的状态问题",
        ),
        (
            "SHOP-BUG-004",
            "账户枚举缺陷中错误提示如何拼接账户信息？",
            "登录失败分支直接拼接账户查询结果",
            "定位账户枚举缺陷的提示来源",
        ),
        (
            "SHOP-BUG-005",
            "退款缺陷中读模型仍然展示什么金额？",
            "读模型仍展示原始支付金额",
            "定位退款金额未同步的表现",
        ),
    ]
    for index, (source_id, text, evidence, answer_point) in enumerate(defect_rows, start=1):
        queries.append(
            _query(
                f"P12-DF-{index:03d}",
                Phase12Category.DEFECT_SEARCH,
                text,
                "定位缺陷记录、根因或修复信息",
                source_type="defect",
                source_id=source_id,
                chunk_index=0,
                evidence=evidence,
                answer_points=[answer_point],
            )
        )

    document_rows = [
        (
            "SHOP-DOC-001",
            "订单提交后客户端应该携带什么标识？",
            "客户端应携带草稿订单标识",
            "回答订单幂等的客户端要求",
        ),
        (
            "SHOP-DOC-001",
            "支付回调重复到达时应该按什么去重？",
            "回调处理必须按交易号去重",
            "回答支付回调的去重键",
        ),
        (
            "SHOP-DOC-001",
            "订单状态变化需要记录哪些追踪字段？",
            "订单号、草稿标识、幂等键、交易号和请求追踪 ID",
            "列出订单可观测性字段",
        ),
        (
            "SHOP-DOC-001",
            "日志中可以记录完整卡号和 CVV 吗？",
            "日志中不得记录完整卡号、CVV 或访问令牌",
            "回答日志中的敏感信息限制",
        ),
        (
            "SHOP-DOC-001",
            "发布评审前哪些幂等场景必须通过？",
            "支付超时、重复回调和返回重提三类幂等场景通过",
            "列出发布前的幂等退出条件",
        ),
        (
            "SHOP-DOC-002",
            "支付页面报错时首先要查询什么？",
            "先按订单号查询订单状态、支付记录和网关交易",
            "回答支付失败时的第一步排查",
        ),
        (
            "SHOP-DOC-002",
            "网关超时后应该使用什么键查询交易？",
            "超时后使用原幂等键查询交易状态",
            "回答网关超时后的查询方式",
        ),
        (
            "SHOP-DOC-002",
            "支付金额不一致时应该继续支付吗？",
            "前端显示金额与支付金额不一致时，停止支付并刷新订单快照",
            "回答金额不一致时的处理",
        ),
        (
            "SHOP-DOC-002",
            "账务对账发现差异后可以手工改库吗？",
            "未解决的差异不得通过手工修改数据库掩盖",
            "回答账务差异的处理边界",
        ),
        (
            "SHOP-DOC-002",
            "哪些支付异常需要立即升级值班人员？",
            "立即升级到支付值班人员",
            "回答支付异常的升级条件",
        ),
        (
            "SHOP-DOC-003",
            "邮箱和密码可以记录在错误日志里吗？",
            "不得在页面源码、分析事件或错误日志中记录明文",
            "回答凭据日志的隐私要求",
        ),
        (
            "SHOP-DOC-003",
            "已注册和未注册账户的失败提示应该一致吗？",
            "已注册与未注册账户的失败提示应一致",
            "回答失败提示的一致性要求",
        ),
        (
            "SHOP-DOC-003",
            "客服解锁账户需要留下什么记录？",
            "客服解锁需要审计记录",
            "回答客服解锁的审计要求",
        ),
        (
            "SHOP-DOC-003",
            "退出登录只删除本地 Cookie 够吗？",
            "退出登录后服务端会话立即失效",
            "回答退出登录的服务端要求",
        ),
        (
            "SHOP-DOC-003",
            "多设备登录时应该向用户展示什么？",
            "应展示最近会话并支持远程退出",
            "回答多设备会话管理能力",
        ),
        (
            "SHOP-DOC-003",
            "验收证据可以只靠口头确认吗？",
            "仅有口头确认不能作为通过证据",
            "回答验收证据的有效性",
        ),
        (
            "SHOP-DOC-001",
            "结算页一次可以应用几个优惠码？",
            "结算页一次只允许应用一个优惠码",
            "回答优惠码数量限制",
        ),
        (
            "SHOP-DOC-001",
            "标准配送低于三百元时运费是多少？",
            "低于三百元收取十二元",
            "回答标准配送的运费规则",
        ),
        (
            "SHOP-DOC-002",
            "重复成功回调会重复记账吗？",
            "重复成功回调返回成功响应但不得重复记账",
            "回答重复回调的记账语义",
        ),
        (
            "SHOP-DOC-003",
            "会话列表可以展示完整 IP 地址吗？",
            "会话列表不得暴露完整 IP 地址",
            "回答会话列表的隐私边界",
        ),
    ]
    for index, (source_id, text, evidence, answer_point) in enumerate(document_rows, start=1):
        queries.append(
            _query(
                f"P12-DOC-{index:03d}",
                Phase12Category.DOCUMENT_QA,
                text,
                "基于规范文档回答流程、约束或验收要求",
                source_type="document",
                source_id=source_id,
                chunk_index=0,
                evidence=evidence,
                answer_points=[answer_point],
            )
        )

    cross_version_rows = [
        (
            "SHOP-LOGIN-003",
            "v2.3 登录跳转为什么不能把令牌放在 URL？",
            "地址栏不出现访问令牌",
            {"version": "v2.3"},
            "比较旧版本登录用例的安全要求",
        ),
        (
            "SHOP-BUG-004",
            "v2.3 的登录失败提示为什么可能泄露账户存在？",
            "未使用统一的安全提示文案",
            {"version": "v2.3"},
            "比较旧版本账户枚举缺陷",
        ),
        (
            "SHOP-LOGIN-002",
            "v2.4 连续登录失败会触发什么保护？",
            "账户锁定三十分钟",
            {"version": "v2.4"},
            "对比新版本登录锁定策略",
        ),
        (
            "SHOP-REQ-001",
            "v2.4 对 URL 中的访问令牌有什么要求？",
            "成功登录后不得在 URL 中暴露访问令牌",
            {"version": "v2.4"},
            "对比新版本认证需求",
        ),
        (
            "SHOP-PAYMENT-002",
            "v2.4 支付超时重试应该使用什么幂等键？",
            "同一幂等键只对应一笔成功扣款",
            {"version": "v2.4"},
            "对比新版本支付幂等要求",
        ),
        (
            "SHOP-BUG-001",
            "v2.4 支付重复扣款缺陷修复后要求怎样重试？",
            "超时后查询原交易状态而不是盲目重试",
            {"version": "v2.4"},
            "对比缺陷修复后的重试策略",
        ),
        (
            "SHOP-REFUND-001",
            "v2.4 部分退款后订单金额应如何变化？",
            "订单显示已退款二十元",
            {"version": "v2.4"},
            "对比退款用例与旧版本行为",
        ),
        (
            "SHOP-REQ-005",
            "v2.4 部分退款是否应该把订单标记为全部退款？",
            "部分退款不得把订单状态误改为全部退款",
            {"version": "v2.4"},
            "对比退款需求中的状态语义",
        ),
        (
            "SHOP-LOGIN-003",
            "旧版本登录用例的预期结果是否仍适用于 v2.4？",
            "会话标识仅存在于安全 Cookie 中",
            {"version": "v2.3"},
            "比较跨版本会话安全要求",
        ),
        (
            "SHOP-BUG-005",
            "v2.4 退款缺陷修复后订单净额如何重算？",
            "读模型按事件重算",
            {"version": "v2.4"},
            "比较退款缺陷与当前净额要求",
        ),
    ]
    for index, (source_id, text, evidence, filters, intent) in enumerate(
        cross_version_rows,
        start=1,
    ):
        source_type = (
            "defect"
            if source_id.startswith("SHOP-BUG")
            else ("requirement" if source_id.startswith("SHOP-REQ") else "testcase")
        )
        queries.append(
            _query(
                f"P12-CV-{index:03d}",
                Phase12Category.CROSS_VERSION,
                text,
                intent,
                source_type=source_type,
                source_id=source_id,
                chunk_index=0,
                evidence=evidence,
                answer_points=["区分版本条件后回答对应行为"],
                filters=filters,
            )
        )

    multi_condition_rows = [
        (
            "SHOP-PAYMENT-002",
            "payment 模块 P0 的支付超时用例要求什么？",
            "同一幂等键只对应一笔成功扣款",
            {"module": "payment", "priority": "P0"},
            "testcase",
        ),
        (
            "SHOP-CHECKOUT-002",
            "checkout 模块 P0 的幂等回归用例验证什么？",
            "只产生一个待支付订单",
            {"module": "checkout", "priority": "P0", "tags": ["regression"]},
            "testcase",
        ),
        (
            "SHOP-LOGIN-002",
            "login 模块 P1 的安全用例如何验证锁定？",
            "账户锁定三十分钟",
            {"module": "login", "priority": "P1", "tags": ["security"]},
            "testcase",
        ),
        (
            "SHOP-CART-001",
            "cart 模块 P1 的边界用例如何处理库存？",
            "不能进入支付页",
            {"module": "cart", "priority": "P1", "tags": ["boundary"]},
            "testcase",
        ),
        (
            "SHOP-REFUND-001",
            "refund 模块 P1 的账务一致性用例检查什么？",
            "账务流水与订单金额完全一致",
            {"module": "refund", "priority": "P1", "tags": ["ledger"]},
            "testcase",
        ),
        (
            "SHOP-BUG-001",
            "payment 模块 P0 的重复扣款缺陷根因是什么？",
            "客户端每次提交都生成新幂等键",
            {"module": "payment", "priority": "P0", "severity": "critical"},
            "defect",
        ),
        (
            "SHOP-BUG-004",
            "login 模块 P2 的开放缺陷有什么安全影响？",
            "未使用统一的安全提示文案",
            {"module": "login", "priority": "P2", "status": "open"},
            "defect",
        ),
        (
            "SHOP-BUG-005",
            "refund 模块 P0 的 reopened 缺陷影响什么？",
            "没有更新订单的净额投影",
            {"module": "refund", "priority": "P0", "status": "reopened"},
            "defect",
        ),
        (
            "SHOP-REQ-003",
            "payment 模块 P0 的支付幂等需求要求什么？",
            "同一订单的支付请求必须使用稳定幂等键",
            {"module": "payment", "priority": "P0"},
            "requirement",
        ),
        (
            "SHOP-REQ-004",
            "payment 模块 P1 的优惠码需求如何限制叠加？",
            "一个订单最多使用一个优惠码",
            {"module": "payment", "priority": "P1"},
            "requirement",
        ),
    ]
    for index, (source_id, text, evidence, filters, source_type) in enumerate(
        multi_condition_rows,
        start=1,
    ):
        queries.append(
            _query(
                f"P12-MC-{index:03d}",
                Phase12Category.MULTI_CONDITION,
                text,
                "组合模块、优先级、状态或标签条件定位知识",
                source_type=source_type,
                source_id=source_id,
                chunk_index=0,
                evidence=evidence,
                answer_points=["先应用全部过滤条件，再回答命中的知识"],
                filters=filters,
            )
        )

    unanswerable_rows = [
        ("系统是否支持按用户画像自动生成黑五优惠券？", "识别语料未覆盖的营销能力"),
        ("退款是否可以使用比特币原路退回？", "识别语料未覆盖的退款币种"),
        ("登录是否支持企业微信扫码？", "识别语料未覆盖的认证方式"),
        ("购物车商品能否直接分享到短视频平台？", "识别语料未覆盖的分享渠道"),
        ("支付失败后系统会自动发送短信吗？", "识别语料未覆盖的通知能力"),
        ("订单是否支持数字人民币硬钱包？", "识别语料未覆盖的支付方式"),
        ("系统能否自动生成跨项目自动化测试脚本？", "识别语料未覆盖的测试生成能力"),
        ("知识平台是否支持 Slack 消息推送？", "识别语料未覆盖的外部集成"),
        ("文档检索是否支持按作者权限隔离？", "识别语料未覆盖的权限模型"),
        ("系统是否提供移动端原生应用？", "识别语料未覆盖的客户端形态"),
    ]
    for index, (text, intent) in enumerate(unanswerable_rows, start=1):
        queries.append(_unanswerable(f"P12-UN-{index:03d}", text, intent))

    return queries


def build_candidate_dataset(corpus: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    """生成候选文件内容；调用方负责写入，避免测试产生副作用。"""
    dataset = load_dataset(corpus)
    documents = retrieve_chunks(dataset)
    if not documents:
        raise ValueError("Phase 12 语料不能为空")
    queries = _candidate_queries()
    by_source: dict[tuple[str, str], list[Any]] = {}
    for document in documents:
        by_source.setdefault((document.source_type, document.source_id), []).append(document)
    for query in queries:
        for gold_chunk in query["gold_chunks"]:
            candidates = by_source.get((gold_chunk["source_type"], gold_chunk["source_id"]), [])
            needle = normalize_text(gold_chunk["evidence"])
            matching = [item for item in candidates if needle in normalize_text(item.content)]
            if not matching:
                raise ValueError(
                    f"{query['query_id']} 的候选证据无法定位到 Chunk："
                    f"{gold_chunk['source_type']}:{gold_chunk['source_id']}"
                )
            gold_chunk["chunk_index"] = min(item.chunk_index for item in matching)
    return {
        "version": "2026-10-20",
        "description": (
            "Phase 12 AI 候选 Query；全部 pending，未经人工确认，不得用于正式 Gold Set 指标。"
        ),
        "corpus_dataset": corpus.name,
        "generation_method": "deterministic-ai-candidate-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "queries": queries,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Phase 12 Evaluation Dataset 候选生成 / 校验")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--mode", choices=["candidate", "formal"], default="candidate")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true", help="只校验现有数据集，不写文件")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    if args.check:
        try:
            dataset = load_phase12_dataset(
                args.dataset, corpus=load_dataset(args.corpus), mode=args.mode
            )
        except Phase12DatasetError as exc:
            print(f"Phase 12 {args.mode} 校验失败：{exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        counts = category_counts(dataset)
        print(
            f"dataset={args.dataset} queries={len(dataset.queries)} "
            f"mode={args.mode} sha256={dataset.dataset_sha256[:12]} "
            f"categories={ {key.value: value for key, value in counts.items()} }"
        )
        return

    if args.mode == "formal":
        parser.error("formal 模式只允许 --check，不能生成或覆盖 Gold Set")

    payload = build_candidate_dataset(Path(args.corpus))
    if args.dry_run:
        print(
            f"queries={len(payload['queries'])} "
            f"categories={ {category.value: CATEGORY_COUNTS[category] for category in Phase12Category} } "
            f"review_status=pending output={args.output or DEFAULT_DATASET}"
        )
        return

    output = Path(args.output or DEFAULT_DATASET)
    corpus = load_dataset(args.corpus)
    try:
        dataset = Phase12Dataset.model_validate(payload)
        validate_phase12_dataset(dataset, corpus, mode="candidate")
    except Phase12DatasetError as exc:
        print(f"Phase 12 候选校验失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print(
        f"wrote={output} queries={len(dataset.queries)} "
        f"sha256={dataset.dataset_sha256[:12]} review_status=pending"
    )


if __name__ == "__main__":
    main()
