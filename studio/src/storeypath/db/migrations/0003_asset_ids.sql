-- Items' IDs are asset tags (format 0.8): ten random symbols and a check symbol,
-- 7K2Q-XM9F-4DP, of no project (an item is its project's by its row) and not
-- numbered by it. The project's item counter is gone, and an item's ID is the key of
-- its row in the whole Studio: a new item's ID is drawn again when one has it, and an
-- item of another project is never written over. Items already here keep the IDs they
-- have (of the form PROJECT-I000142, the project's in them): a project made again, or
-- opened again from a package of format 0.8, has tags.
ALTER TABLE projects DROP COLUMN IF EXISTS next_item_seq;
ALTER TABLE items DROP CONSTRAINT items_pkey;
ALTER TABLE items ADD PRIMARY KEY (id);
