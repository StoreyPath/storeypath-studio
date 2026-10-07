package storeypath

import (
	"fmt"
	"regexp"
	"strings"
)

// Levels of an ID, from its first segment to its last:
// PROJECT-LOCATION-BUILDING-FLOOR-OBJECT, e.g. K7Q2XM-RUH-HQ-F02-0142.
const (
	LevelProject  = "project"
	LevelLocation = "location"
	LevelBuilding = "building"
	LevelFloor    = "floor"
	LevelObject   = "object"
)

var levels = []string{LevelProject, LevelLocation, LevelBuilding, LevelFloor, LevelObject}

var segmentRE = regexp.MustCompile(`^[A-Z0-9]{1,16}$`)

// The longest an ID can be: five segments of 16, and their hyphens; an item's, the
// project's segment, a hyphen and its seven. A longer string is refused before it
// is taken apart.
const (
	maxIDLength     = 5*16 + 4
	maxItemIDLength = 16 + 1 + 7
)

// ID is a StoreyPath ID taken apart. Every prefix of an ID is itself an ID: the
// location, building and floor an object belongs to.
type ID struct {
	Segments []string
}

var itemCodeRE = regexp.MustCompile(`^I\d{6}$`)

// IsItemID says whether an ID is an item's (format 0.6): the project's code and the
// item's own number, I and six digits (K7Q2XM-I000142). An item's ID is not part of
// the place hierarchy: ParseID reads it as two segments, and its floor is in the
// item, not in its ID.
func IsItemID(value string) bool {
	if len(value) > maxItemIDLength {
		return false
	}
	project, code, ok := strings.Cut(value, "-")
	return ok && segmentRE.MatchString(project) && itemCodeRE.MatchString(code)
}

// ParseID checks an ID and takes it apart.
func ParseID(value string) (ID, error) {
	if len(value) > maxIDLength {
		return ID{}, fmt.Errorf("%q: an ID is at most %d characters", clip(value), maxIDLength)
	}
	segments := strings.Split(value, "-")
	if len(segments) < 1 || len(segments) > len(levels) {
		return ID{}, fmt.Errorf("%q: an ID has 1 to %d segments", value, len(levels))
	}
	for _, s := range segments {
		if !segmentRE.MatchString(s) {
			return ID{}, fmt.Errorf("%q: segment %q is not 1-16 upper-case letters or digits", value, s)
		}
	}
	return ID{Segments: segments}, nil
}

// String is the ID as written.
func (id ID) String() string { return strings.Join(id.Segments, "-") }

// Level is what the ID names: project, location, building, floor or object.
func (id ID) Level() string { return levels[len(id.Segments)-1] }

// Project is the ID's first segment, the project's code.
func (id ID) Project() string { return id.Segments[0] }

// Code is the ID's last segment: the object's own code within its parent.
func (id ID) Code() string { return id.Segments[len(id.Segments)-1] }

// Parent is the ID of what this one is part of ("" for a project).
func (id ID) Parent() string { return strings.Join(id.Segments[:len(id.Segments)-1], "-") }

// Prefix is the ID of the ancestor at a level (the ID itself at its own level).
func (id ID) Prefix(level string) (string, error) {
	for depth, l := range levels {
		if l == level {
			if depth+1 > len(id.Segments) {
				return "", fmt.Errorf("%s has no %s segment", id, level)
			}
			return strings.Join(id.Segments[:depth+1], "-"), nil
		}
	}
	return "", fmt.Errorf("unknown level %q", level)
}
