package storeypath

import (
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"sync"
)

// Reading the JSON files of a package as strictly as every reader must, so that
// they all read the same thing: within the limits, and by keys as written.

// decodeJSON reads a JSON file into v, once no list in it is longer than max.
func decodeJSON(data []byte, v any, max int) error {
	if err := checkLists(data, max); err != nil {
		return err
	}
	return unmarshal(data, v)
}

// decode reads a feature collection into features of one kind, of at most max.
func decode[T any](data []byte, kind string, max int, out *[]*T, set func(*T, string, *Geometry)) error {
	if err := checkLists(data, max); err != nil {
		return err
	}
	var fc struct {
		Type     string            `json:"type"`
		Features []json.RawMessage `json:"features"`
	}
	if err := unmarshal(data, &fc); err != nil {
		return err
	}
	if fc.Type != "FeatureCollection" {
		return fmt.Errorf("not a FeatureCollection")
	}
	for i, raw := range fc.Features {
		var f struct {
			ID         string          `json:"id"`
			Geometry   *Geometry       `json:"geometry"`
			Properties json.RawMessage `json:"properties"`
		}
		var k struct {
			Kind string `json:"kind"`
		}
		err := unmarshal(raw, &f)
		if err == nil {
			err = unmarshal(f.Properties, &k)
		}
		if err != nil {
			return fmt.Errorf("feature %d (%s): %w", i, clip(f.ID), err)
		}
		if k.Kind != kind {
			return fmt.Errorf("feature %d (%s): kind %q, expected %q", i, clip(f.ID), clip(k.Kind), kind)
		}
		v := new(T)
		if err := unmarshal(f.Properties, v); err != nil {
			return fmt.Errorf("feature %d (%s): %w", i, clip(f.ID), err)
		}
		set(v, f.ID, f.Geometry)
		*out = append(*out, v)
	}
	return nil
}

// unmarshal is json.Unmarshal, but refuses a key that names a field of v only in
// another case: encoding/json would read "Hidden" into Hidden (the last of
// "hidden" and "Hidden" winning), where every other reader keeps to "hidden".
func unmarshal(data []byte, v any) error {
	if err := checkKeys(data, reflect.TypeOf(v)); err != nil {
		return err
	}
	return json.Unmarshal(data, v)
}

// checkKeys fails at a key of the JSON value in data that is, but for its case,
// the key of a field of t, at any depth.
func checkKeys(data []byte, t reflect.Type) error {
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	if !holdsKeys(t) {
		return nil
	}
	switch t.Kind() {
	case reflect.Struct:
		fields := keysOf(t)
		return members(data, func(key string, value []byte) error {
			if ft, ok := fields[key]; ok {
				return checkKeys(value, ft)
			}
			for name := range fields {
				if strings.EqualFold(key, name) { // as encoding/json matches them
					return fmt.Errorf("key %q is %q written in another case", clip(key), name)
				}
			}
			return nil
		})
	case reflect.Slice, reflect.Array:
		return elements(data, func(value []byte) error { return checkKeys(value, t.Elem()) })
	case reflect.Map:
		return members(data, func(_ string, value []byte) error { return checkKeys(value, t.Elem()) })
	}
	return nil
}

var unmarshalerType = reflect.TypeFor[json.Unmarshaler]()

// holdsKeys says whether a value of t is or holds a struct read by its keys.
func holdsKeys(t reflect.Type) bool {
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	switch t.Kind() {
	case reflect.Struct:
		return !reflect.PointerTo(t).Implements(unmarshalerType)
	case reflect.Slice, reflect.Array, reflect.Map:
		return holdsKeys(t.Elem())
	}
	return false
}

var structKeys sync.Map // reflect.Type → map[string]reflect.Type

// keysOf is the keys of a struct's fields (those of a struct embedded in it too),
// with their types. A field without a json tag is not one of the format's keys
// (a feature's ID and Geometry are the feature's, not its properties').
func keysOf(t reflect.Type) map[string]reflect.Type {
	if keys, ok := structKeys.Load(t); ok {
		return keys.(map[string]reflect.Type)
	}
	keys := map[string]reflect.Type{}
	var add func(t reflect.Type)
	add = func(t reflect.Type) {
		for i := range t.NumField() {
			f := t.Field(i)
			tag := f.Tag.Get("json")
			if f.Anonymous && tag == "" && f.Type.Kind() == reflect.Struct {
				add(f.Type)
				continue
			}
			name, _, _ := strings.Cut(tag, ",")
			if !f.IsExported() || tag == "" || name == "-" {
				continue
			}
			if name == "" {
				name = f.Name
			}
			keys[name] = f.Type
		}
	}
	add(t)
	structKeys.Store(t, keys)
	return keys
}

// members calls fn with each key of a JSON object and its value as written in
// data, nothing copied. It does nothing for a value that is not an object, and
// stops where data is not JSON: the decoder that follows refuses it.
func members(data []byte, fn func(key string, value []byte) error) error {
	i := skipSpace(data, 0)
	if i == len(data) || data[i] != '{' {
		return nil
	}
	for i++; ; i++ {
		i = skipSpace(data, i)
		if i == len(data) || data[i] != '"' {
			return nil
		}
		end := skipValue(data, i)
		key, ok := unquote(data[i:end])
		if !ok {
			return nil
		}
		if i = skipSpace(data, end); i == len(data) || data[i] != ':' {
			return nil
		}
		i = skipSpace(data, i+1)
		end = skipValue(data, i)
		if err := fn(key, data[i:end]); err != nil {
			return err
		}
		if i = skipSpace(data, end); i == len(data) || data[i] != ',' {
			return nil
		}
	}
}

// elements calls fn with each value of a JSON list as written in data, as
// members does for an object.
func elements(data []byte, fn func(value []byte) error) error {
	i := skipSpace(data, 0)
	if i == len(data) || data[i] != '[' {
		return nil
	}
	for i++; ; i++ {
		i = skipSpace(data, i)
		end := skipValue(data, i)
		if end == i { // the list's end, or not JSON
			return nil
		}
		if err := fn(data[i:end]); err != nil {
			return err
		}
		if i = skipSpace(data, end); i == len(data) || data[i] != ',' {
			return nil
		}
	}
}

func skipSpace(data []byte, i int) int {
	for i < len(data) && (data[i] == ' ' || data[i] == '\t' || data[i] == '\n' || data[i] == '\r') {
		i++
	}
	return i
}

// skipValue is where the JSON value at i ends (i itself at a list's or object's
// end, or where there is no value).
func skipValue(data []byte, i int) int {
	if i == len(data) {
		return i
	}
	switch data[i] {
	case '"':
		for i++; i < len(data); i++ {
			switch data[i] {
			case '\\':
				i++
			case '"':
				return i + 1
			}
		}
		return len(data)
	case '{', '[':
		depth := 0
		for ; i < len(data); i++ {
			switch data[i] {
			case '"':
				i = skipValue(data, i) - 1
			case '{', '[':
				depth++
			case '}', ']':
				if depth--; depth == 0 {
					return i + 1
				}
			}
		}
		return len(data)
	}
	for ; i < len(data); i++ { // a number, true, false or null
		switch data[i] {
		case ',', '}', ']', ':', ' ', '\t', '\n', '\r':
			return i
		}
	}
	return i
}

// unquote is a JSON string's text.
func unquote(s []byte) (string, bool) {
	if len(s) < 2 || s[0] != '"' || s[len(s)-1] != '"' {
		return "", false
	}
	inner := s[1 : len(s)-1]
	for _, c := range inner {
		if c == '\\' { // escaped: as encoding/json reads it
			var out string
			err := json.Unmarshal(s, &out)
			return out, err == nil
		}
	}
	return string(inner), true
}

// maxDepth: how deeply the lists and objects of a JSON file may nest (as deeply as
// encoding/json reads).
const maxDepth = 10000

// checkLists fails when a list in a JSON file has more than max entries, before it
// is decoded: what decoding costs is in proportion to its entries. It counts the
// commas of each list, outside strings; whether the file is JSON at all is left to
// the decoder.
func checkLists(data []byte, max int) error {
	var commas []int // for each list or object open, the commas in it so far (-1: an object)
	for i := 0; i < len(data); i++ {
		switch data[i] {
		case '"':
			for i++; i < len(data) && data[i] != '"'; i++ {
				if data[i] == '\\' {
					i++
				}
			}
		case '[', '{':
			if len(commas) == maxDepth {
				return errors.New("nested too deeply")
			}
			if data[i] == '[' {
				commas = append(commas, 0)
			} else {
				commas = append(commas, -1)
			}
		case ']', '}':
			if len(commas) > 0 {
				commas = commas[:len(commas)-1]
			}
		case ',':
			if n := len(commas); n > 0 && commas[n-1] >= 0 {
				if commas[n-1]++; commas[n-1] >= max {
					return fmt.Errorf("%w: a list of more than %d entries", ErrTooLarge, max)
				}
			}
		}
	}
	return nil
}
