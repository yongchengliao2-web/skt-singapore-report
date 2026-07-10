# AGENTS.md

## Project

This repository is a reusable analysis workspace for SKT Singapore. It follows the same static report pattern as the earlier NEXTPRIME workspace: configuration files describe the data contract, Python pipelines normalize the source data, and generated HTML is written to `output/` and `site/`.

## Brand Context

SKT is a mature beauty and skincare brand. Default analysis should be business-readable, Chinese, and decision-oriented.

## Fixed Project Data Source

The fixed live project data source is:

https://docs.google.com/spreadsheets/d/1d5dBa6AJsJNNcA23NoNJd4OX3douJ4gWmHa94vJdRpk/edit?usp=sharing

Before any review, read this sheet unless the user explicitly provides another source.

## Required Initial Scope

The first version should support:

- Platform GMV trend, using `SP店铺实收GMV` and `TT-销售GMV`.
- Offsite media signal from `站外数据源`.
- Onsite ad spend and ad GMV from `站内广告`.
- Product traffic, sales, add-to-cart, and category performance from `站内产品数据-skt`.
- Product/category unit supplements from `SP-销量` and `TT-销量` when needed.

## Field Rule

For platform GMV:

- SP uses `GMV(Customer Payment)` from `SP店铺实收GMV`, with exchange rate from `品类表`.
- TT uses `GMV(After seller discounts) RMB` from `TT-销售GMV`.

Do not substitute onsite product GMV or onsite ad GMV for platform GMV.

## Engineering Rules

- Normalize source data into daily records before charting.
- Keep source field mapping explicit in `config/data_contract.yaml`.
- Save generated reports to `output/` and copy the public page to `site/`.
- Do not hardcode conclusions that are not supported by the current source data.

