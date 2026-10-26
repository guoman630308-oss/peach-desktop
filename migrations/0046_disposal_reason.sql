ALTER TABLE asset ADD COLUMN disposal_reason TEXT
CHECK(disposal_reason IS NULL OR disposal_reason IN ('manual','junk','duplicate','missing'));
