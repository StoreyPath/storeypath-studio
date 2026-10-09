package storeypath

import (
	_ "embed"
	"encoding/json"
	"regexp"
)

// Finishes (format 0.9): the floors and walls a room may be given, a fixed set every
// reader has (spec/finishes.json; finishes.json here is a copy of it, made by
// spec/finishes.mjs). A space or zone may name its floor finish (Space.FloorFinish,
// Zone.FloorFinish) and a space its walls' (Space.WallFinish); nil, or a code this
// module does not know (a later version's), is its type's default. A zone with none
// takes its space's floor finish, else its own type's default.

//go:embed finishes.json
var finishesJSON []byte

// Finish is one of StoreyPath's finishes.
type Finish struct {
	// Code: kept for good, never given to another finish (FLOOR-CARPET-NAVY).
	Code string `json:"code"`
	// Applies: "floor" or "wall".
	Applies string `json:"applies"`
	// Group: what it is shown among (carpet, paint, …: FinishGroups).
	Group  string `json:"group"`
	Name   string `json:"name"`
	NameAr string `json:"name_ar"`
	// Tone: its colour on the whole (#rrggbb).
	Tone      string  `json:"tone"`
	Roughness float64 `json:"roughness"`
	// SizeM: the metres its painted image covers a side; Paint: what the viewer paints
	// it with (its kind and values).
	SizeM float64        `json:"size_m"`
	Paint map[string]any `json:"paint"`
}

// FinishGroup is a group finishes are shown in.
type FinishGroup struct {
	Code    string `json:"code"`
	Applies string `json:"applies"`
	Name    string `json:"name"`
	NameAr  string `json:"name_ar"`
}

// FinishList is StoreyPath's finishes, as spec/finishes.json has them.
type FinishList struct {
	Version int `json:"version"`
	// Exterior: the finish of walls' faces outside every room.
	Exterior string        `json:"exterior"`
	Groups   []FinishGroup `json:"groups"`
	Finishes []Finish      `json:"finishes"`
	// Defaults: by "floor" and "wall", the finish of each type of space when it is given none.
	Defaults map[string]map[string]string `json:"defaults"`
}

var finishList = func() *FinishList {
	var l FinishList
	if err := json.Unmarshal(finishesJSON, &l); err != nil {
		panic("storeypath: finishes.json: " + err.Error())
	}
	return &l
}()

var finishByCode = func() map[string]*Finish {
	out := map[string]*Finish{}
	for i := range finishList.Finishes {
		out[finishList.Finishes[i].Code] = &finishList.Finishes[i]
	}
	return out
}()

// Finishes returns StoreyPath's finishes (not to be changed).
func Finishes() *FinishList { return finishList }

// FinishOf returns a finish by its code, or nil.
func FinishOf(code string) *Finish { return finishByCode[code] }

// DefaultFinish returns the finish a type of space has when it is given none, for
// "floor" or "wall" (an unknown type: as unspecified).
func DefaultFinish(applies, spaceType string) string {
	table := finishList.Defaults[applies]
	if code, ok := table[spaceType]; ok {
		return code
	}
	return table["unspecified"]
}

// known returns a code when it is one of the list's for applies, else "".
func known(code *string, applies string) string {
	if code == nil {
		return ""
	}
	if f := FinishOf(*code); f != nil && f.Applies == applies {
		return f.Code
	}
	return ""
}

// FloorFinishOf returns the finish a space's floor shows: its own, else its type's.
func (s *Space) FloorFinishOf() string {
	if c := known(s.FloorFinish, "floor"); c != "" {
		return c
	}
	return DefaultFinish("floor", s.Type)
}

// WallFinishOf returns the finish of a space's walls: its own, else its type's.
func (s *Space) WallFinishOf() string {
	if c := known(s.WallFinish, "wall"); c != "" {
		return c
	}
	return DefaultFinish("wall", s.Type)
}

// FloorFinishOf returns the finish a zone's floor shows: its own, else its space's
// (space may be nil), else its own type's default.
func (z *Zone) FloorFinishOf(space *Space) string {
	if c := known(z.FloorFinish, "floor"); c != "" {
		return c
	}
	if space != nil {
		if c := known(space.FloorFinish, "floor"); c != "" {
			return c
		}
	}
	return DefaultFinish("floor", z.Type)
}

// finishCode: the form of a finish's code, for floors and for walls (a code a later
// version adds has it too).
var finishCode = map[string]*regexp.Regexp{
	"floor": regexp.MustCompile(`^FLOOR(-[A-Z0-9]+)+$`),
	"wall":  regexp.MustCompile(`^WALL(-[A-Z0-9]+)+$`),
}

// wellFormedFinish says whether a code has the form of a finish for applies.
func wellFormedFinish(code, applies string) bool {
	return len(code) <= 40 && finishCode[applies].MatchString(code)
}
