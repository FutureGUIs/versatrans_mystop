# Changelog

Each version's section becomes the notes of its GitHub release.

## 0.5.1

- Student devices link to the school device with `via_device_id`, removing the Home Assistant deprecation warning about `via_device` ahead of its removal in 2027.8.

## 0.5.0

- Each student is now a child device of a school device named after the district.
- The school device replaces the earlier "polling controls" device and keeps its identifier, so upgrades rename it in place and keep all entity IDs.

## 0.4.5

- Day codes like `MTHF` are read as Monday, Tuesday, Thursday and Friday. Previously `TH` was read as Thursday and Tuesday was dropped.

## 0.4.4

- The polling status and schedule status sensors show plain-language text in the UI. Automations still see the same underlying values.
- When a schedule has several issues, the sensor shows the most important one and lists all of them in the new `schedule_issues` attribute.

## 0.4.3

- First release installable through HACS.
