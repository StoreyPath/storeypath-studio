-- Vertical stacks (format 0.8): a person's link of a lift, stairs or escalator to the same
-- one on other floors, kept with the space's other corrections: the ID of the space on
-- another floor it is linked with, '' for linked with none; NULL: linked as found (the
-- same object code, or footprints overlapping: stacks.py).
ALTER TABLE overrides ADD COLUMN IF NOT EXISTS stack text CHECK (length(stack) <= 100);
