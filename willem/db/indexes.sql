CREATE INDEX IF NOT EXISTS idx_synced ON transactions(synced);
CREATE INDEX IF NOT EXISTS idx_type ON transactions(type);
CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at_utc);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id);
