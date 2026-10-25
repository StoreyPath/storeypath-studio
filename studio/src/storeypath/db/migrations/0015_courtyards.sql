-- Courtyards and light wells (extract.find_courtyards): a space set aside as no room is open
-- to the sky, a hole in its floor's outline, as a person says: true a courtyard or a light
-- well; false not (a shaft, a gap: part of the floor); NULL as read (the object's
-- detected_sky, kept in its more).
ALTER TABLE overrides ADD COLUMN IF NOT EXISTS sky boolean;
