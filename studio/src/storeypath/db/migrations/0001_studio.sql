-- StoreyPath Studio's own database: every project whole (its tree, objects, corrections,
-- items, drawings and the packages exported of it), the organization's item types, the
-- accounts and who may do what, and the history of every change. Geometry is in each
-- building's own frame (local metres, SRID 0); the GeoJSON as the project has it is kept
-- exactly (packages are compared by hashes of it), with a PostGIS geometry made from it
-- beside it for finding things by where they are.

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;

-- a GeoJSON geometry as PostGIS's (local metres, SRID 0); NULL for none, or for one
-- PostGIS cannot read (an empty ring a drawing gave)
CREATE FUNCTION storeypath.geom(g jsonb) RETURNS geometry
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
BEGIN
    IF g IS NULL OR jsonb_typeof(g) <> 'object' OR g = '{}'::jsonb THEN
        RETURN NULL;
    END IF;
    RETURN ST_SetSRID(ST_GeomFromGeoJSON(g::text), 0);
EXCEPTION WHEN others THEN
    RETURN NULL;
END $$;

-- the order rows of a project were made in (a project's objects, corrections, items,
-- readings are listed as they were added, as its file lists them)
CREATE SEQUENCE storeypath.positions;

-- ---- accounts (accounts.py) -------------------------------------------------------

CREATE TABLE storeypath.users (
    id text PRIMARY KEY,
    username text NOT NULL UNIQUE,
    name text NOT NULL DEFAULT '',
    role text NOT NULL CHECK (role IN ('admin', 'engineer', 'user')),
    capabilities jsonb NOT NULL DEFAULT '[]',
    active boolean NOT NULL DEFAULT true,
    password text NOT NULL,
    must_change_password boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL,
    password_changed_at timestamptz,
    last_login_at timestamptz,
    session_epoch integer NOT NULL DEFAULT 0
);

-- a project's owner, by its code: kept apart from the project itself, as who a code was
-- shared with is kept before a project is opened and after it is gone
CREATE TABLE storeypath.project_access (
    code text PRIMARY KEY,
    owner text REFERENCES storeypath.users (id)
);

CREATE TABLE storeypath.grants (
    id bigserial PRIMARY KEY,
    project text NOT NULL REFERENCES storeypath.project_access (code) ON DELETE CASCADE,
    scope_kind text NOT NULL CHECK (scope_kind IN ('project', 'building', 'floor')),
    scope_id text NOT NULL DEFAULT '',
    user_id text NOT NULL REFERENCES storeypath.users (id),
    level text NOT NULL CHECK (level IN ('view', 'edit', 'share')),
    given_by text REFERENCES storeypath.users (id),
    given_at timestamptz NOT NULL,
    UNIQUE (project, scope_kind, scope_id, user_id)
);
CREATE INDEX grants_of_user ON storeypath.grants (user_id);

-- who is logged in: the SHA-256 of each session's token (never the token); times are
-- the clock's seconds
CREATE TABLE storeypath.sessions (
    token_hash text PRIMARY KEY,
    user_id text NOT NULL REFERENCES storeypath.users (id) ON DELETE CASCADE,
    created double precision NOT NULL,
    last_seen double precision NOT NULL,
    expires double precision NOT NULL,
    epoch integer NOT NULL,
    address text
);
CREATE INDEX sessions_of_user ON storeypath.sessions (user_id);

CREATE TABLE storeypath.audit (
    id bigserial PRIMARY KEY,
    at timestamptz NOT NULL,
    user_id text,
    username text,
    address text,
    action text NOT NULL,
    target text,
    outcome text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'
);

-- ---- projects --------------------------------------------------------------------

CREATE TABLE storeypath.projects (
    code text PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL,
    next_item_seq integer NOT NULL DEFAULT 1,
    format_version integer NOT NULL DEFAULT 1,
    version bigint NOT NULL DEFAULT 1,  -- one more at every change of the project
    changed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE storeypath.locations (
    project text NOT NULL REFERENCES storeypath.projects (code) ON DELETE CASCADE,
    code text NOT NULL,
    position integer NOT NULL,
    name text NOT NULL,
    address text,
    placement jsonb,  -- the site on the map
    site_origin jsonb,
    PRIMARY KEY (project, code)
);

CREATE TABLE storeypath.buildings (
    project text NOT NULL,
    location text NOT NULL,
    code text NOT NULL,
    position integer NOT NULL,
    name text NOT NULL,
    placement jsonb,  -- on the map by itself
    site jsonb,  -- on its location's site plan
    next_object_seq integer NOT NULL DEFAULT 1,
    PRIMARY KEY (project, location, code),
    FOREIGN KEY (project, location) REFERENCES storeypath.locations (project, code) ON DELETE CASCADE
);

CREATE TABLE storeypath.floors (
    id text NOT NULL UNIQUE,  -- PROJECT-LOCATION-BUILDING-FLOOR
    project text NOT NULL,
    location text NOT NULL,
    building text NOT NULL,
    code text NOT NULL,
    position integer NOT NULL,
    name text NOT NULL,
    ordinal integer NOT NULL,
    elevation double precision NOT NULL,
    height double precision NOT NULL,
    parapet_height double precision,
    source jsonb,  -- the drawing it is read from (workspace.SourceDrawing)
    outline jsonb,
    outline_geom geometry GENERATED ALWAYS AS (storeypath.geom(outline)) STORED,
    converted_at timestamptz,
    method text,
    warnings jsonb NOT NULL DEFAULT '[]',
    layers jsonb NOT NULL DEFAULT '[]',
    walls jsonb,  -- as drawn, with door and window gaps
    wall_thickness double precision,
    symbols jsonb,
    symbols_key text,
    edits jsonb NOT NULL DEFAULT '{}',  -- what a person drew on it in review
    version bigint NOT NULL DEFAULT 1,  -- one more at every change of it or of anything on it
    PRIMARY KEY (project, location, building, code),
    FOREIGN KEY (project, location, building) REFERENCES storeypath.buildings (project, location, code)
        ON DELETE CASCADE
);
CREATE INDEX floors_where ON storeypath.floors USING gist (outline_geom);

-- spaces, zones and openings, active and retired (an ID is never given again)
CREATE TABLE storeypath.objects (
    project text NOT NULL REFERENCES storeypath.projects (code) ON DELETE CASCADE,
    id text NOT NULL,
    floor text NOT NULL,  -- its floor's ID (kept by an ID retired with its floor)
    position bigint NOT NULL DEFAULT nextval('storeypath.positions'),
    kind text NOT NULL CHECK (kind IN ('space', 'zone', 'opening')),
    type text NOT NULL,  -- as detected; a correction wins over it
    type_source text NOT NULL,
    name text,
    number text,
    label text,
    geometry jsonb NOT NULL,
    geom geometry GENERATED ALWAYS AS (storeypath.geom(geometry)) STORED,
    -- the rest: connects, parent, zones, span, width, swings, sill, height, tag, issues,
    -- detected_ignored (those set)
    more jsonb NOT NULL DEFAULT '{}',
    status text NOT NULL CHECK (status IN ('active', 'retired')),
    created_at timestamptz NOT NULL,
    retired_at timestamptz,
    PRIMARY KEY (project, id)
);
CREATE INDEX objects_of_floor ON storeypath.objects (project, floor);
CREATE INDEX objects_where ON storeypath.objects USING gist (geom);

-- a person's corrections, by object
CREATE TABLE storeypath.overrides (
    project text NOT NULL REFERENCES storeypath.projects (code) ON DELETE CASCADE,
    object text NOT NULL,
    floor text NOT NULL,
    position bigint NOT NULL DEFAULT nextval('storeypath.positions'),
    type text,
    name text,
    number text,
    hidden boolean,
    ignored boolean,
    capacity integer CHECK (capacity BETWEEN 0 AND 10000),
    changed_by text,  -- a user's id
    changed_at timestamptz NOT NULL DEFAULT now(),
    version bigint NOT NULL DEFAULT 1,
    PRIMARY KEY (project, object)
);
CREATE INDEX overrides_of_floor ON storeypath.overrides (project, floor);

-- furniture and equipment
CREATE TABLE storeypath.items (
    project text NOT NULL REFERENCES storeypath.projects (code) ON DELETE CASCADE,
    id text NOT NULL,
    floor text NOT NULL,  -- '' for one retired in a package whose floor is not known
    position bigint NOT NULL DEFAULT nextval('storeypath.positions'),
    type text NOT NULL,
    x double precision NOT NULL,
    y double precision NOT NULL,
    rotation double precision NOT NULL DEFAULT 0,
    details jsonb NOT NULL DEFAULT '{}',  -- its StoreyPath fields (Item.values)
    geom geometry GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(x, y), 0)) STORED,
    status text NOT NULL CHECK (status IN ('active', 'retired')),
    created_at timestamptz NOT NULL,
    retired_at timestamptz,
    changed_by text,
    changed_at timestamptz NOT NULL DEFAULT now(),
    version bigint NOT NULL DEFAULT 1,
    PRIMARY KEY (project, id)
);
CREATE INDEX items_of_floor ON storeypath.items (project, floor);
CREATE INDEX items_where ON storeypath.items USING gist (geom);

-- what the text model read a drawing's texts as, by text
CREATE TABLE storeypath.readings (
    project text NOT NULL REFERENCES storeypath.projects (code) ON DELETE CASCADE,
    text text NOT NULL,
    position bigint NOT NULL DEFAULT nextval('storeypath.positions'),
    type text,
    source text NOT NULL,
    rooms_only boolean NOT NULL DEFAULT false,
    asked text,
    PRIMARY KEY (project, text)
);

-- what the vision model saw, by room shape
CREATE TABLE storeypath.vision (
    project text NOT NULL REFERENCES storeypath.projects (code) ON DELETE CASCADE,
    key text NOT NULL,
    position bigint NOT NULL DEFAULT nextval('storeypath.positions'),
    answer jsonb NOT NULL,
    PRIMARY KEY (project, key)
);

-- the drawings as sent (or as kept without their private information), and those sent
-- and waiting for a person to say what of them to keep (incoming)
CREATE TABLE storeypath.drawings (
    project text NOT NULL REFERENCES storeypath.projects (code) ON DELETE CASCADE,
    name text NOT NULL,
    incoming boolean NOT NULL DEFAULT false,
    bytes bytea NOT NULL,
    size bigint NOT NULL,
    sha256 text NOT NULL,
    words text,  -- every word and string left in it (privacy.words), once read
    uploaded_by text,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project, name)
);

-- the project's export history (workspace.ExportRecord), and the bytes of each package as
-- it was sent; a package kept with no record of it (made before records) has no position
CREATE TABLE storeypath.exports (
    id bigserial PRIMARY KEY,
    project text NOT NULL REFERENCES storeypath.projects (code) ON DELETE CASCADE,
    position integer,
    sequence integer,
    file text NOT NULL,
    record jsonb,
    bytes bytea,
    size bigint,
    sha256 text,
    made_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project, position)
);
CREATE INDEX exports_by_file ON storeypath.exports (project, file);

-- the organization's item types (catalogue.py), and what else is the whole Studio's
CREATE TABLE storeypath.catalogue (
    code text PRIMARY KEY,
    position integer NOT NULL,
    type jsonb NOT NULL
);
CREATE TABLE storeypath.settings (
    key text PRIMARY KEY,
    value jsonb NOT NULL
);

-- every change: who made it, what it changed, as it was and as it became
CREATE TABLE storeypath.history (
    seq bigserial PRIMARY KEY,
    project text NOT NULL REFERENCES storeypath.projects (code) ON DELETE CASCADE,
    version bigint NOT NULL,  -- the project's version it made
    at timestamptz NOT NULL DEFAULT now(),
    who jsonb NOT NULL,  -- {id, username, name}, {"local": true} or "command line"
    part text NOT NULL,  -- object, item, edit, floor, building, project
    kind text NOT NULL,
    floors text[] NOT NULL DEFAULT '{}',
    targets text[] NOT NULL DEFAULT '{}',
    before jsonb,
    after jsonb,
    undoes bigint REFERENCES storeypath.history (seq),
    redoes bigint REFERENCES storeypath.history (seq),
    more jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX history_of_project ON storeypath.history (project, seq);
CREATE INDEX history_of_floors ON storeypath.history USING gin (floors);
CREATE INDEX history_of_targets ON storeypath.history USING gin (targets);

-- one editor a floor at a time (Phase D)
CREATE TABLE storeypath.floor_locks (
    floor text PRIMARY KEY REFERENCES storeypath.floors (id) ON DELETE CASCADE,
    project text NOT NULL REFERENCES storeypath.projects (code) ON DELETE CASCADE,
    who text REFERENCES storeypath.users (id) ON DELETE CASCADE,  -- NULL: this computer
    session text,
    since timestamptz NOT NULL DEFAULT now(),
    last_seen timestamptz NOT NULL DEFAULT now()
);
