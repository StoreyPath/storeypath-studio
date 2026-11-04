-- Furniture layouts (workspace.Furnishing): the items of a room kept to be placed again in
-- any room of the project, each piece from the corner of the room's box it was taken from.
CREATE TABLE IF NOT EXISTS storeypath.furnishings (
    project text NOT NULL REFERENCES storeypath.projects (code) ON DELETE CASCADE,
    id text NOT NULL CHECK (length(id) <= 40),
    position bigint NOT NULL DEFAULT nextval('storeypath.positions'),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    floor text NOT NULL,
    room text CHECK (length(room) <= 200),
    width double precision NOT NULL,
    depth double precision NOT NULL,
    pieces jsonb NOT NULL,
    created_by text CHECK (length(created_by) <= 200),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project, id)
);
