# RPM Living NRT — Object Inventory

**Project:** `gds-prototype-20190629` · **Dataset:** `rpm_living_nrt` · **Pulled:** 18 August 2026

145 objects: 108 tables and 37 views. 27 of the tables are `_bckup_` view-backing copies, listed separately at the end.

Views are marked **(v)**.

## Contents

- [Occupancy and PMS](#occupancy-and-pms) — 13
- [Contact, journey and attribution](#contact-journey-and-attribution) — 18
- [Conversion: triple, velocity and ratio](#conversion-triple-velocity-and-ratio) — 12
- [Impact and benchmarks](#impact-and-benchmarks) — 10
- [Email and Hayley](#email-and-hayley) — 20
- [Ads, social and reputation](#ads-social-and-reputation) — 19
- [GA4 and tracking](#ga4-and-tracking) — 10
- [Vendor and marketing spend](#vendor-and-marketing-spend) — 6
- [Reference and mapping](#reference-and-mapping) — 10
- [Backup tables](#backup-tables) — 27

---

## Occupancy and PMS

*13 objects*

| Object | Type |
|---|---|
| `t_oc_agg_occupancy_property` | View |
| `t_oc_agg_occupancy_operational` | Table |
| `t_oc_agg_occupancy_projection` | Table |
| `t_occupancy_rate` | View |
| `t_occupancy_exposure_rate` | Table |
| `t_ot_agg_resident_activity_property` | Table |
| `t_pms_info` | Table |
| `t_pms_resident_activity` | Table |
| `t_pms_resident_activity_snapshot` | View |
| `t_pms_unit_snapshot` | View |
| `t_pms_contacts_mapping` | View |
| `t_pms_conversion_ratio_monthly` | View |
| `t_pms_conversion_ratio_weekly` | View |

## Contact, journey and attribution

*18 objects*

| Object | Type |
|---|---|
| `t_contact_activity` | Table |
| `t_contact_activity_hayley` | View |
| `t_contact_first_website_url` | View |
| `contact_info` | Table |
| `prospect_journey` | Table |
| `pai_journey_1747307582311553649` | Table |
| `pai_journey_` | Table |
| `t_pai_journey_card` | View |
| `t_applications_journey` | View |
| `t_application_metrics_summary` | View |
| `pai_attribution_setup_audit_table` | View |
| `t_crstal_events` | Table |
| `t_crstal_ms` | Table |
| `t_crstal_source` | View |
| `t_pai_crstal_top_metrics` | Table |
| `t_pai_crstal_contact_details` | Table |
| `t_pai_crstal_breakdown_by_source_medium` | Table |
| `t_nonleasing_reasons` | View |

## Conversion: triple, velocity and ratio

*12 objects*

| Object | Type |
|---|---|
| `conversion_triple_base_v1` | Table |
| `conversion_triple_base_hayley_v1` | View |
| `conversion_velocity_base` | Table |
| `conversion_velocity_base_hayley` | View |
| `pai_conversion_triple_org_quarterly` | Table |
| `pai_conversion_triple_property_quarterly` | View |
| `pai_conversion_velocity_org_quarterly` | Table |
| `pai_conversion_velocity_org_quarterly_cdp` | Table |
| `pai_conversion_velocity_property_quarterly` | View |
| `pai_conversion_velocity_property_quarterly_cdp` | Table |
| `pai_conversion_ratio_org_quarterly_cdp` | Table |
| `pai_conversion_ratio_property_quarterly_cdp` | Table |

## Impact and benchmarks

*10 objects*

| Object | Type |
|---|---|
| `impact_click_application_base` | Table |
| `impact_click_application_benchmark` | Table |
| `impact_click_application_benchmark_product` | Table |
| `impact_click_scheduled_toured_base` | Table |
| `impact_click_scheduled_toured_benchmark` | Table |
| `impact_click_scheduled_toured_benchmark_product` | Table |
| `hyreferral_friends_lifecycle_base` | Table |
| `hyreferral_friends_lifecycle_benchmark` | Table |
| `hytours_summary` | Table |
| `hytours_benchmarks` | Table |

## Email and Hayley

*20 objects*

| Object | Type |
|---|---|
| `all_orgs_hayley_chat_benchmarks` | Table |
| `all_orgs_hayley_chat_metrics` | Table |
| `all_orgs_hayley_mail_activity` | Table |
| `all_orgs_hayley_mail_benchmark` | Table |
| `hayley_chat_metrics_base_p2` | Table |
| `hayley_email_engagement_events` | Table |
| `allorgs_performance_emails` | Table |
| `allorgs_performance_hyevent_metrics` | Table |
| `amps_all_emails` | Table |
| `amps_all_emails_v2` | Table |
| `amps_all_products` | Table |
| `amps_all_products_v2` | Table |
| `brag_email_reporting` | Table |
| `email_allorgs_conversion_new` | Table |
| `email_growth_conversion_new` | Table |
| `email_growth_influence_new` | Table |
| `email_growth_influence_combined` | Table |
| `product_allorgs_influence_combined` | View |
| `products_allorgs_influence_new` | View |
| `subject_lines_analysis` | Table |

## Ads, social and reputation

*19 objects*

| Object | Type |
|---|---|
| `t_ad_agg_hylydim1_property` | Table |
| `t_ad_agg_hylydim2_region` | Table |
| `t_ad_agg_hylydim3_org` | Table |
| `t_ad_agg_nativedim0_ad` | Table |
| `t_ad_agg_nativedim1_ad_group` | Table |
| `t_ad_agg_nativedim2_campaign` | Table |
| `t_ad_agg_native2dim0_adgroup` | Table |
| `t_ad_agg_native2dim1_campaign` | Table |
| `t_ad_agg_native2dim2_conversion` | Table |
| `t_ad_agg_native3dim0_ad` | Table |
| `t_so_agg_hylydim1_region` | View |
| `t_so_agg_hylydim2_org` | View |
| `t_so_agg_nativedim0_post` | View |
| `t_so_agg_nativedim1_page` | View |
| `t_opi_agg_native1_dim0_review` | View |
| `t_rep_birdeye_agg_native1_dim0_review` | Table |
| `t_ora_agg_hylydim1_ora` | Table |
| `t_ora_agg_hylydim2_ora` | Table |
| `t_ora_agg_hylydim3_ora` | Table |

## GA4 and tracking

*10 objects*

| Object | Type |
|---|---|
| `ga4_analytics_events` | Table |
| `t_ga4_events_first_visit` | Table |
| `t_ga4_users_first_visit` | Table |
| `t_ga4_sources` | Table |
| `ga_campaign_cost_info` | View |
| `ga_hyly_mti` | View |
| `tracking_phone_pai` | View |
| `tracking_source_tags` | Table |
| `t_google_internal_social_sites` | Table |
| `t_ils_data_reporting` | Table |

## Vendor and marketing spend

*6 objects*

| Object | Type |
|---|---|
| `vendor_list` | Table |
| `vendors_data` | View |
| `vendor_invoice` | View |
| `vendor_spend_ledger` | View |
| `mktg_budgets` | Table |
| `mktg_tags` | View |

## Reference and mapping

*10 objects*

| Object | Type |
|---|---|
| `property_info` | Table |
| `deleted_properties` | Table |
| `tenants_info` | Table |
| `t_property_mapper` | View |
| `t_property_tz` | View |
| `t_org_analysis` | Table |
| `t_user_avatars` | View |
| `t_halo_subject_data_sources` | View |
| `t_smart_da_standard` | Table |
| `t_smart_da_demandx` | Table |

## Backup tables

*27 objects.* Each carries the `_bckup_` suffix and shadows a view of the same base name. Every one of the 27 has a corresponding view in this dataset.

| Backup table | Shadows view |
|---|---|
| `ga_campaign_cost_info_bckup_` | `ga_campaign_cost_info` |
| `ga_hyly_mti_bckup_` | `ga_hyly_mti` |
| `mktg_tags_bckup_` | `mktg_tags` |
| `pai_attribution_setup_audit_table_bckup_` | `pai_attribution_setup_audit_table` |
| `pai_conversion_triple_property_quarterly_bckup_` | `pai_conversion_triple_property_quarterly` |
| `pai_conversion_velocity_property_quarterly_bckup_` | `pai_conversion_velocity_property_quarterly` |
| `product_allorgs_influence_combined_bckup_` | `product_allorgs_influence_combined` |
| `products_allorgs_influence_new_bckup_` | `products_allorgs_influence_new` |
| `t_contact_first_website_url_bckup_` | `t_contact_first_website_url` |
| `t_crstal_source_bckup_` | `t_crstal_source` |
| `t_halo_subject_data_sources_bckup_` | `t_halo_subject_data_sources` |
| `t_nonleasing_reasons_bckup_` | `t_nonleasing_reasons` |
| `t_oc_agg_occupancy_property_bckup_` | `t_oc_agg_occupancy_property` |
| `t_occupancy_rate_bckup_` | `t_occupancy_rate` |
| `t_opi_agg_native1_dim0_review_bckup_` | `t_opi_agg_native1_dim0_review` |
| `t_pai_journey_card_bckup_` | `t_pai_journey_card` |
| `t_property_mapper_bckup_` | `t_property_mapper` |
| `t_property_tz_bckup_` | `t_property_tz` |
| `t_so_agg_hylydim1_region_bckup_` | `t_so_agg_hylydim1_region` |
| `t_so_agg_hylydim2_org_bckup_` | `t_so_agg_hylydim2_org` |
| `t_so_agg_nativedim0_post_bckup_` | `t_so_agg_nativedim0_post` |
| `t_so_agg_nativedim1_page_bckup_` | `t_so_agg_nativedim1_page` |
| `t_user_avatars_bckup_` | `t_user_avatars` |
| `tracking_phone_pai_bckup_` | `tracking_phone_pai` |
| `vendor_invoice_bckup_` | `vendor_invoice` |
| `vendor_spend_ledger_bckup_` | `vendor_spend_ledger` |
| `vendors_data_bckup_` | `vendors_data` |

---

*Inventory of `gds-prototype-20190629.rpm_living_nrt`, 145 objects, retrieved 18 August 2026.*
