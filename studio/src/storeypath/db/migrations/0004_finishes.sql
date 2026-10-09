-- Finishes (format 0.9): what a room's floor and (a space's) walls are finished in, set by a
-- person in review, kept with the room's other corrections: a code of StoreyPath's fixed
-- set (spec/finishes.json: FLOOR-… for floors, WALL-… for walls); NULL, its type's
-- default. Changed, undone and redone like every correction (history, part "object",
-- kind "finish"), by whoever may edit the room's floor.
ALTER TABLE overrides ADD COLUMN IF NOT EXISTS floor_finish text
    CHECK (floor_finish ~ '^FLOOR(-[A-Z0-9]+)+$' AND length(floor_finish) <= 40);
ALTER TABLE overrides ADD COLUMN IF NOT EXISTS wall_finish text
    CHECK (wall_finish ~ '^WALL(-[A-Z0-9]+)+$' AND length(wall_finish) <= 40);
