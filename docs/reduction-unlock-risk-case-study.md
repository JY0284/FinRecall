# FinRecall Case Study: Share Reduction And Unlock Risk Query

Date: 2026-06-23

## Case

Query:

```text
华峰测控 688200 减持 限售解禁 风险 2026年6月
```

Expected agent behavior: return the concrete disclosure/article body that lets
the agent answer whether there is share-reduction or unlock-related risk, not
only navigation pages.

Current FinRecall result after commit `79114f3`:

| Rank | Source | Title | Content Length | Problem |
| --- | --- | --- | ---: | --- |
| 1 | `sse_notice` | 华峰测控(688200) 2026年6月 公司公告 信息披露 - 上海证券交易所 | 171 | Portal entry only |
| 2 | `cninfo_notice` | 华峰测控(688200) 2026年6月 公告披露 - 巨潮资讯 | 153 | Portal entry only |
| 3 | `eastmoney_stock_profile` | 华峰测控(688200) 2026年6月 业绩 估值 业务分析 - 东方财富F10 | 90 | Research entry only |

## Verified External Ground Truth

The relevant disclosure exists outside FinRecall's current result set:

- Sina disclosure page:
  `https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?CompanyCode=80955092&gather=1&id=12355654`
- Shanghai Securities News page:
  `https://paper.cnstock.com/html/2026-05/27/content_2222303.htm`

FinRecall's current extractor can parse both pages into useful bodies:

| URL | Extracted Title | Extracted Content Length | Key Facts Extracted |
| --- | --- | ---: | --- |
| Sina | 华峰测控：股东询价转让计划书 | 2097 | 1,355,596 shares, 1.00% of total share capital, not secondary-market reduction, 6-month lock-up |
| Shanghai Securities News | 北京华峰测控技术股份有限公司股东询价转让计划书 | 2135 | Same key disclosure facts |

Important extracted facts:

- The transferring shareholder is 中国时代远望科技有限公司.
- Planned transfer size is 1,355,596 shares, equal to 1.00% of total share capital.
- The transaction is an inquiry transfer, not centralized bidding or block trading.
- The disclosure states it is not a secondary-market reduction.
- Acquired shares cannot be transferred within 6 months after acquisition.
- The transferees are institutional investors with pricing and risk-bearing capability.

## Root Cause

This is not mainly a summarization or extraction failure. It is a discovery and
intent-routing failure.

### 1. Hybrid Does Not Run Keyless For This Query

`HybridSearchProvider._should_run_keyless()` triggers keyless recall for terms
such as `新闻`, `公告`, `财报`, `政策`, `市场震荡`, and `解读`.

The user query contains:

```text
减持 限售解禁 风险
```

Those terms are disclosure/event terms, but they are not currently treated as
content-query triggers in hybrid. Therefore hybrid never asks the keyless
harvester to search the wider web and only returns native entry pages.

### 2. Native SSE Document Search Uses Overly Literal Keywords

The native query plan is correct in broad shape:

```text
stock_codes = ["688200"]
company_names = ["华峰测控"]
date_text = "2026年6月"
intents = ["announcement", "news"]
date_range = 2026-05-01..2026-06-30
announcement_keywords = ["减持", "限售解禁"]
```

But the concrete disclosure title is:

```text
股东询价转让计划书
```

Direct SSE API probes for `减持`, `限售解禁`, `询价转让`, `转让`, `股东`,
`股份`, `限售`, `解除限售`, `上市流通`, and `风险` returned zero rows for
the tested `queryCompanyBulletin.do` path and date window. So the current native
official-document path degrades to generic exchange and CNINFO portals.

### 3. Keyless Search Can Misfire On Similar Company Names

Running `KeylessSearchHarvester` directly on the original query returned Sina
articles about `华丰科技`, not `华峰测控`. The issue is a near-homophone /
near-shape company-name false positive. Simply enabling keyless for disclosure
event terms would add recall but could introduce wrong-company results unless
hard filters require exact company or ticker evidence.

### 4. Existing Extraction Is Sufficient Once A Body URL Is Found

The extractor produced 2k+ character bodies from Sina and Shanghai Securities
News. This means the next fix should focus on URL discovery, event synonym
expansion, and relevance validation rather than building another parser first.

## Design Implications

FinRecall currently has three different result types mixed in one list:

1. Concrete article/disclosure bodies that the agent can use directly.
2. Official portal/navigation entries that are authoritative but thin.
3. Market data/profile pages that are useful for navigation but not sufficient
   for reasoning-heavy answers.

For disclosure-event queries, result type 1 must be preferred. Portal entries
should only be fallback results and should be marked as such.

## Recommended Fix Plan

### Phase 1: Intent And Trigger Correction

Add disclosure event terms to hybrid content-query triggers:

```text
减持 限售 解禁 限售解禁 询价转让 转让计划 股东 风险提示 异动 异常波动
```

This should cause hybrid to run keyless for event-risk queries while leaving
pure quote/fund data queries unchanged.

### Phase 2: Native Event Synonym Expansion

Map query terms to disclosure-title synonyms before querying official sources:

| Query Term | Search Synonyms |
| --- | --- |
| 减持 | 减持, 询价转让, 转让计划, 股东询价转让 |
| 限售解禁 | 限售, 解除限售, 上市流通 |
| 风险 | 风险提示, 交易风险, 异常波动 |

Do not stop after the first empty keyword. Search several synonyms and dedupe.

### Phase 3: Secondary Disclosure Article Fallback

If official exchange API returns only portals or no rows, add a secondary
disclosure article search/fetch fallback for known reliable mirrors:

- Sina company disclosure detail pages.
- Shanghai Securities News announcement pages.
- Potentially CNINFO/SSE static PDF links when discoverable.

The fallback should only emit a result when extracted text contains exact ticker
`688200` or exact company name `华峰测控`.

### Phase 4: Hard Relevance Guard

Before accepting keyless or fallback article results for stock-specific
disclosure queries, require:

- Exact ticker match, or
- Exact company name match plus at least one event term.

This blocks results like `华丰科技` for a query about `华峰测控`.

### Phase 5: Result Type Metadata

Add metadata such as:

```json
{
  "result_kind": "disclosure_body" | "official_portal" | "market_data" | "article_body"
}
```

Ranking can then strongly prefer `disclosure_body` / `article_body` for
event-risk questions and demote `official_portal` to fallback.

## Regression Tests To Add

1. Original query returns a body result:

```text
华峰测控 688200 减持 限售解禁 风险 2026年6月
```

Expected top result contains:

```text
股东询价转让计划书
1,355,596股
不属于通过二级市场减持
6个月内不得转让
```

2. Wrong-company guard:

For query `华峰测控 688200 ...`, reject results that only mention `华丰科技`
without `688200` or `华峰测控`.

3. Portal fallback ordering:

When no body exists, official portals may be returned, but any concrete body
with exact company/ticker evidence outranks them.

4. Performance guard:

Fallback body discovery should run only for event-risk/disclosure queries, with
small result limits and timeouts. It must not run for pure data queries like:

```text
黄金ETF 518880 最新净值 单位净值
```

## Suggested Next Implementation Target

The highest-impact next change is:

1. Extend hybrid content triggers for disclosure event terms.
2. Add exact ticker/company hard filter for stock-event keyless results.
3. Add a disclosure-body fallback source for Sina / Shanghai Securities News
   only when native official document discovery fails.

That directly addresses this failure without broadening search behavior for all
queries.

## Implementation Update

Implemented after this analysis:

- Hybrid now treats disclosure event terms such as `减持`, `限售`, `解禁`,
  `限售解禁`, `询价转让`, `转让计划`, `异动`, and `异常波动` as content
  triggers.
- Stock-specific disclosure-event candidates must mention the exact ticker or
  company name. This blocks near-name mistakes such as returning `华丰科技`
  for a `华峰测控 688200` query.
- Native disclosure discovery now expands deterministic event synonyms, e.g.
  `减持 -> 询价转让 / 转让计划 / 股东询价转让` and
  `限售解禁 -> 限售 / 解除限售 / 上市流通`.
- For explicit disclosure-event queries, native discovery first checks Sina's
  company announcement list and extracts matching announcement bodies. If this
  fails, it falls back to the SSE document API.
- If native already has a concrete announcement body, hybrid skips keyless
  search to avoid slower and noisier web recall.
- Content enrichment skips exchange/CNINFO portal URLs so it does not turn
  navigation pages into misleading long generic content.

Fresh live result after implementation:

```text
elapsed ~= 3.4s
top source = sina_notice_body
top content length = 2178
top title = 华峰测控：股东询价转让结果报告书暨持股5%以上股东权益变动触及5%整数倍的提示性公告
```

The returned body includes event evidence such as `询价转让`, `1,355,596股`,
`减持`, and post-transfer holding ratio `9.92%`.

## Follow-up: Unlock / Listing Expression Gap

Additional live testing showed that a nearby query was still weak:

```text
华峰测控 688200 解除限售 上市流通 2026年6月
```

Before the follow-up fix, FinRecall could misclassify same-company but
different-event disclosures such as convertible-bond allotment results, because
the event relevance score counted company identity terms (`华峰测控`, `688200`)
instead of only event terms.

The follow-up implementation changed the behavior as follows:

- Expanded native and hybrid disclosure-event vocabularies to include
  `解除限售`, `上市流通`, `股票上市`, `股票上市公告`, `限制性股票`, `归属结果`,
  `权益变动`, and `持股5%以上`.
- Added unlock/listing synonyms so queries for `解除限售` or `上市流通` can match
  disclosure titles written as `限制性股票激励计划部分归属结果暨股票上市公告`.
- Changed Sina announcement body selection to collect matching list entries,
  rank them by event relevance, and fetch only the top bounded set of details.
- Tightened event scoring so company names, tickers, and dates no longer make a
  body look event-relevant by themselves.

Fresh live result after the follow-up:

```text
query = 华峰测控 688200 解除限售 上市流通 2026年6月
elapsed ~= 2.5s
top source = sina_notice_body
top content length = 3871
top title = 华峰测控：关于2021年限制性股票激励计划部分归属结果暨股票上市公告
```

The returned body includes `688200`, `华峰测控`, `限制性股票`, `归属结果`,
`股票上市`, `上市流通`, and `26,285股`, while the previously observed
convertible-bond allotment result is rejected for this query.
