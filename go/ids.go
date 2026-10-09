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

// The longest an ID can be: five segments of 16, and their hyphens; an item's of
// formats 0.6 and 0.7, the project's segment, a hyphen and its seven. A longer string
// is refused before it is taken apart.
const (
	maxIDLength           = 5*16 + 4
	maxLegacyItemIDLength = 16 + 1 + 7
)

// ID is a StoreyPath ID taken apart. Every prefix of an ID is itself an ID: the
// location, building and floor an object belongs to.
type ID struct {
	Segments []string
}

// ItemIDAlphabet is Crockford's base32, the symbols of an item's ID (no I, L, O, U).
const ItemIDAlphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

// An item's ID (format 0.8) is ten random symbols and a check symbol, written 4-4-3
// with hyphens (7K2Q-XM9F-4DP); a person may type it in either case, with O for 0, I
// and L for 1, hyphens and spaces anywhere, in at most itemIDTypedMax characters.
const (
	itemIDSymbols  = 10
	itemIDLength   = 13
	itemIDTypedMax = 64
)

// itemValues: each byte's value in the alphabet as written (-1: not a symbol);
// typedValues, as a person may type it (-2: left out).
var itemValues, typedValues = func() (written, typed [256]int8) {
	for i := range written {
		written[i], typed[i] = -1, -1
	}
	for v, c := range []byte(ItemIDAlphabet) {
		written[c], typed[c] = int8(v), int8(v)
		if c >= 'A' && c <= 'Z' {
			typed[c+'a'-'A'] = int8(v)
		}
	}
	for _, c := range []byte("Oo") {
		typed[c] = 0
	}
	for _, c := range []byte("IiLl") {
		typed[c] = 1
	}
	for _, c := range []byte("- \t\n\r\v\f") {
		typed[c] = -2
	}
	return written, typed
}()

// luhnSum is Luhn mod 32 over the values: the 2nd, 4th, … 10th doubled (a doubled
// value's two base-32 digits added: 2v − 31 from 16 on), all added, mod 32. Of ten
// symbols and their check symbol, 0.
func luhnSum(values []int) int {
	sum := 0
	for k, v := range values {
		if k%2 == 1 {
			v *= 2
			v = v/32 + v%32
		}
		sum += v
	}
	return sum % 32
}

// ItemCheckSymbol is the check symbol of an item ID's ten symbols (as written: upper
// case, Crockford's base32), Luhn mod 32 over their values; false when they are not
// ten such symbols.
func ItemCheckSymbol(symbols string) (byte, bool) {
	if len(symbols) != itemIDSymbols {
		return 0, false
	}
	values := make([]int, itemIDSymbols)
	for k := range itemIDSymbols {
		v := itemValues[symbols[k]]
		if v < 0 {
			return 0, false
		}
		values[k] = int(v)
	}
	return ItemIDAlphabet[(32-luhnSum(values))%32], true
}

// IsItemID says whether an ID is an item's as packages write it (format 0.8): an
// asset's tag, ten random symbols and a check symbol of Crockford's base32 in groups
// of four, four and three (7K2Q-XM9F-4DP), the check right. An item's ID is not a
// place's: it says nothing of where the item is, nor of its project (ParseID refuses
// it).
func IsItemID(value string) bool {
	if len(value) != itemIDLength || value[4] != '-' || value[9] != '-' {
		return false
	}
	values := make([]int, 0, itemIDSymbols+1)
	for k := range len(value) {
		if k == 4 || k == 9 {
			continue
		}
		v := itemValues[value[k]]
		if v < 0 {
			return false
		}
		values = append(values, int(v))
	}
	return luhnSum(values) == 0
}

// NormalizeItemID reads an item's ID as a person typed it (a search box, a command
// line, a call made for a person): letters in either case, O read as 0, I and L as 1,
// hyphens and spaces left out wherever they are; eleven symbols with their check right
// are the ID, as written (7k2q xm9f 4dp: 7K2Q-XM9F-4DP). Anything else is not one
// (false). Never for an ID read from a package: that is written so, or is not an ID.
func NormalizeItemID(text string) (string, bool) {
	if len(text) > itemIDTypedMax {
		return "", false
	}
	values := make([]int, 0, itemIDSymbols+1)
	for k := range len(text) {
		switch v := typedValues[text[k]]; {
		case v == -2:
		case v < 0 || len(values) == itemIDSymbols+1:
			return "", false
		default:
			values = append(values, int(v))
		}
	}
	if len(values) != itemIDSymbols+1 || luhnSum(values) != 0 {
		return "", false
	}
	out := make([]byte, 0, itemIDLength)
	for k, v := range values {
		if k == 4 || k == 8 {
			out = append(out, '-')
		}
		out = append(out, ItemIDAlphabet[v])
	}
	return string(out), true
}

var legacyItemCodeRE = regexp.MustCompile(`^I\d{6}$`)

// isLegacyItemID says whether an ID has the form of an item's in formats 0.6 and 0.7:
// the project's code and the item's own number, I and six digits (K7Q2XM-I000142).
func isLegacyItemID(value string) bool {
	if len(value) > maxLegacyItemIDLength {
		return false
	}
	project, code, ok := strings.Cut(value, "-")
	return ok && segmentRE.MatchString(project) && legacyItemCodeRE.MatchString(code)
}

// ParseID checks a place's ID and takes it apart. An item's ID (IsItemID) is not a
// place's: it is refused.
func ParseID(value string) (ID, error) {
	if len(value) > maxIDLength {
		return ID{}, fmt.Errorf("%q: an ID is at most %d characters", clip(value), maxIDLength)
	}
	if IsItemID(value) {
		return ID{}, fmt.Errorf("%q is an item's ID (an asset's), not a place's", value)
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
