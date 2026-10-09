// StoreyPath's finishes (format 0.9): the floors and walls a room may be given, a fixed
// set every reader has. Made from spec/finishes.json by spec/finishes.mjs: do not edit.
//
// A space or zone may name its floor finish (`floor_finish`) and a space its walls'
// (`wall_finish`); null, or a code this list does not have (a later version's), is its
// type's default. A zone with none takes its space's floor finish, else its own type's
// default. A wall's face shows the finish of the room it faces; a face outside every room,
// `exterior`.

/** The list: { version, exterior, groups, finishes, defaults: { floor, wall } by type }. */
export const FINISHES = {
  "about": "StoreyPath's finishes (format 0.9): the floors and walls a room may be given, a fixed set built into every reader. Each is painted by the viewer (viewer/src/world/finishes.js, by its kind and values), nothing downloaded. Codes are kept for good: a finish is never renamed to another code, nor its code given to another. Copies: viewer/src/finishes.js and go/finishes.json, made by spec/finishes.mjs.",
  "version": 1,
  "exterior": "WALL-PAINT-WHITE",
  "groups": [
    {
      "code": "carpet",
      "applies": "floor",
      "name": "Carpet",
      "name_ar": "سجاد"
    },
    {
      "code": "vinyl",
      "applies": "floor",
      "name": "Vinyl",
      "name_ar": "فينيل"
    },
    {
      "code": "porcelain",
      "applies": "floor",
      "name": "Porcelain tiles",
      "name_ar": "بلاط بورسلان"
    },
    {
      "code": "stone",
      "applies": "floor",
      "name": "Marble and terrazzo",
      "name_ar": "رخام وتيرازو"
    },
    {
      "code": "wood",
      "applies": "floor",
      "name": "Wood",
      "name_ar": "خشب"
    },
    {
      "code": "concrete",
      "applies": "floor",
      "name": "Concrete and resin",
      "name_ar": "خرسانة وراتنج"
    },
    {
      "code": "technical",
      "applies": "floor",
      "name": "Technical",
      "name_ar": "أرضيات فنية"
    },
    {
      "code": "paint",
      "applies": "wall",
      "name": "Paint",
      "name_ar": "دهان"
    },
    {
      "code": "wallpaper",
      "applies": "wall",
      "name": "Wallpaper",
      "name_ar": "ورق جدران"
    },
    {
      "code": "wall-tiles",
      "applies": "wall",
      "name": "Tiles",
      "name_ar": "بلاط"
    },
    {
      "code": "cladding",
      "applies": "wall",
      "name": "Wood and stone",
      "name_ar": "خشب وحجر"
    }
  ],
  "finishes": [
    {
      "code": "FLOOR-CARPET-CHARCOAL",
      "applies": "floor",
      "group": "carpet",
      "name": "Carpet tiles, charcoal",
      "name_ar": "بلاط سجاد، فحمي",
      "tone": "#414449",
      "roughness": 1,
      "size_m": 2,
      "paint": {
        "kind": "carpet",
        "base": "#42454a"
      }
    },
    {
      "code": "FLOOR-CARPET-GREY",
      "applies": "floor",
      "group": "carpet",
      "name": "Carpet tiles, mid grey",
      "name_ar": "بلاط سجاد، رمادي متوسط",
      "tone": "#797b7e",
      "roughness": 1,
      "size_m": 2,
      "paint": {
        "kind": "carpet",
        "base": "#7a7c80"
      }
    },
    {
      "code": "FLOOR-CARPET-BLUEGREY",
      "applies": "floor",
      "group": "carpet",
      "name": "Carpet tiles, blue-grey",
      "name_ar": "بلاط سجاد، رمادي مزرق",
      "tone": "#5c616b",
      "roughness": 1,
      "size_m": 2,
      "paint": {
        "kind": "carpet",
        "base": "#5c616a"
      }
    },
    {
      "code": "FLOOR-CARPET-WARMGREY",
      "applies": "floor",
      "group": "carpet",
      "name": "Carpet tiles, warm grey",
      "name_ar": "بلاط سجاد، رمادي دافئ",
      "tone": "#756c64",
      "roughness": 1,
      "size_m": 2,
      "paint": {
        "kind": "carpet",
        "base": "#766c64"
      }
    },
    {
      "code": "FLOOR-CARPET-NAVY",
      "applies": "floor",
      "group": "carpet",
      "name": "Carpet tiles, navy",
      "name_ar": "بلاط سجاد، كحلي",
      "tone": "#303b58",
      "roughness": 1,
      "size_m": 2,
      "paint": {
        "kind": "carpet",
        "base": "#303b58"
      }
    },
    {
      "code": "FLOOR-CARPET-BEIGE",
      "applies": "floor",
      "group": "carpet",
      "name": "Carpet tiles, beige",
      "name_ar": "بلاط سجاد، بيج",
      "tone": "#a7977b",
      "roughness": 1,
      "size_m": 2,
      "paint": {
        "kind": "carpet",
        "base": "#a8987c"
      }
    },
    {
      "code": "FLOOR-CARPET-GREEN",
      "applies": "floor",
      "group": "carpet",
      "name": "Carpet tiles, green",
      "name_ar": "بلاط سجاد، أخضر",
      "tone": "#4d5f4e",
      "roughness": 1,
      "size_m": 2,
      "paint": {
        "kind": "carpet",
        "base": "#4d604e"
      }
    },
    {
      "code": "FLOOR-CARPET-BURGUNDY",
      "applies": "floor",
      "group": "carpet",
      "name": "Carpet tiles, burgundy",
      "name_ar": "بلاط سجاد، عنابي",
      "tone": "#692c34",
      "roughness": 1,
      "size_m": 2,
      "paint": {
        "kind": "carpet",
        "base": "#6a2d35"
      }
    },
    {
      "code": "FLOOR-CARPET-PATTERN",
      "applies": "floor",
      "group": "carpet",
      "name": "Patterned carpet, grey and blue",
      "name_ar": "سجاد منقوش، رمادي وأزرق",
      "tone": "#616973",
      "roughness": 1,
      "size_m": 2,
      "paint": {
        "kind": "carpetPattern",
        "base": "#686c72",
        "accent": "#3d5a80"
      }
    },
    {
      "code": "FLOOR-CARPET-PRAYER",
      "applies": "floor",
      "group": "carpet",
      "name": "Prayer carpet, rows",
      "name_ar": "سجاد صلاة بصفوف",
      "tone": "#466b4c",
      "roughness": 1,
      "size_m": 2.4,
      "paint": {
        "kind": "prayer",
        "base": "#2e5c45",
        "accent": "#c9a861"
      }
    },
    {
      "code": "FLOOR-VINYL-GREY",
      "applies": "floor",
      "group": "vinyl",
      "name": "Vinyl sheet, grey",
      "name_ar": "فينيل لفائف، رمادي",
      "tone": "#a6a7a7",
      "roughness": 0.4,
      "size_m": 2,
      "paint": {
        "kind": "vinyl",
        "base": "#a6a7a7",
        "speckle": [
          "#6e7072",
          "#d6d6d2",
          "#8a8c8f"
        ]
      }
    },
    {
      "code": "FLOOR-VINYL-BLUE",
      "applies": "floor",
      "group": "vinyl",
      "name": "Vinyl sheet, clinic blue",
      "name_ar": "فينيل لفائف، أزرق طبي",
      "tone": "#8eaabd",
      "roughness": 0.35,
      "size_m": 2,
      "paint": {
        "kind": "vinyl",
        "base": "#8eaabd",
        "speckle": [
          "#5f7f97",
          "#d2dfe6",
          "#7895aa"
        ]
      }
    },
    {
      "code": "FLOOR-LVT-OAK",
      "applies": "floor",
      "group": "vinyl",
      "name": "Vinyl planks, light oak look",
      "name_ar": "ألواح فينيل بمظهر البلوط الفاتح",
      "tone": "#b69a78",
      "roughness": 0.35,
      "size_m": 2.4,
      "paint": {
        "kind": "planks",
        "base": "#c8aa86",
        "width_m": 0.2,
        "grain": 0.16,
        "gloss": 0.3
      }
    },
    {
      "code": "FLOOR-LVT-WALNUT",
      "applies": "floor",
      "group": "vinyl",
      "name": "Vinyl planks, walnut look",
      "name_ar": "ألواح فينيل بمظهر الجوز",
      "tone": "#6c4e37",
      "roughness": 0.35,
      "size_m": 2.4,
      "paint": {
        "kind": "planks",
        "base": "#7a583e",
        "width_m": 0.2,
        "grain": 0.2,
        "gloss": 0.3
      }
    },
    {
      "code": "FLOOR-PORCELAIN-WHITE",
      "applies": "floor",
      "group": "porcelain",
      "name": "Porcelain tiles 60×60, white",
      "name_ar": "بلاط بورسلان 60×60، أبيض",
      "tone": "#e8e7e3",
      "roughness": 0.22,
      "size_m": 1.2,
      "paint": {
        "kind": "tiles",
        "base": "#e8e7e3",
        "grout": "#b9b6b0",
        "tile_m": [
          0.6,
          0.6
        ],
        "vein": 0.05
      }
    },
    {
      "code": "FLOOR-PORCELAIN-GREY",
      "applies": "floor",
      "group": "porcelain",
      "name": "Porcelain tiles 60×60, light grey",
      "name_ar": "بلاط بورسلان 60×60، رمادي فاتح",
      "tone": "#d9d7d2",
      "roughness": 0.25,
      "size_m": 1.2,
      "paint": {
        "kind": "tiles",
        "base": "#d8d6d1",
        "grout": "#a4a19b",
        "tile_m": [
          0.6,
          0.6
        ],
        "vein": 0.07
      }
    },
    {
      "code": "FLOOR-PORCELAIN-BEIGE",
      "applies": "floor",
      "group": "porcelain",
      "name": "Porcelain tiles 60×60, beige",
      "name_ar": "بلاط بورسلان 60×60، بيج",
      "tone": "#d0c8bc",
      "roughness": 0.25,
      "size_m": 1.2,
      "paint": {
        "kind": "tiles",
        "base": "#cec6ba",
        "grout": "#a39b8f",
        "tile_m": [
          0.6,
          0.6
        ],
        "vein": 0.07
      }
    },
    {
      "code": "FLOOR-PORCELAIN-DARK",
      "applies": "floor",
      "group": "porcelain",
      "name": "Porcelain tiles 60×120, dark grey",
      "name_ar": "بلاط بورسلان 60×120، رمادي داكن",
      "tone": "#5c5d5e",
      "roughness": 0.3,
      "size_m": 2.4,
      "paint": {
        "kind": "tiles",
        "base": "#5c5d5e",
        "grout": "#46474a",
        "tile_m": [
          1.2,
          0.6
        ],
        "offset": 0.5,
        "vein": 0.09
      }
    },
    {
      "code": "FLOOR-MARBLE-WHITE",
      "applies": "floor",
      "group": "stone",
      "name": "Marble, white",
      "name_ar": "رخام أبيض",
      "tone": "#e2e2df",
      "roughness": 0.12,
      "size_m": 1.6,
      "paint": {
        "kind": "marble",
        "base": "#ebeae7",
        "vein": "#8d9196",
        "veins": 0.8,
        "tile_m": 0.8
      }
    },
    {
      "code": "FLOOR-MARBLE-BEIGE",
      "applies": "floor",
      "group": "stone",
      "name": "Marble, beige",
      "name_ar": "رخام بيج",
      "tone": "#d9caad",
      "roughness": 0.12,
      "size_m": 1.6,
      "paint": {
        "kind": "marble",
        "base": "#ddcfb3",
        "vein": "#a88c62",
        "veins": 0.45,
        "tile_m": 0.8
      }
    },
    {
      "code": "FLOOR-MARBLE-BLACK",
      "applies": "floor",
      "group": "stone",
      "name": "Marble, black",
      "name_ar": "رخام أسود",
      "tone": "#2f2f30",
      "roughness": 0.1,
      "size_m": 1.6,
      "paint": {
        "kind": "marble",
        "base": "#262628",
        "vein": "#d8d8d4",
        "veins": 0.7,
        "tile_m": 0.8
      }
    },
    {
      "code": "FLOOR-TERRAZZO-LIGHT",
      "applies": "floor",
      "group": "stone",
      "name": "Terrazzo, light",
      "name_ar": "تيرازو فاتح",
      "tone": "#d6d2cb",
      "roughness": 0.22,
      "size_m": 1.5,
      "paint": {
        "kind": "terrazzo",
        "binder": "#e2ded6",
        "chips": "light"
      }
    },
    {
      "code": "FLOOR-TERRAZZO-DARK",
      "applies": "floor",
      "group": "stone",
      "name": "Terrazzo, dark",
      "name_ar": "تيرازو داكن",
      "tone": "#5f5f61",
      "roughness": 0.2,
      "size_m": 1.5,
      "paint": {
        "kind": "terrazzo",
        "binder": "#55565a",
        "chips": "dark"
      }
    },
    {
      "code": "FLOOR-WOOD-OAK",
      "applies": "floor",
      "group": "wood",
      "name": "Oak planks, light",
      "name_ar": "ألواح بلوط فاتح",
      "tone": "#aa855f",
      "roughness": 0.5,
      "size_m": 2.4,
      "paint": {
        "kind": "planks",
        "base": "#ba9268",
        "width_m": 0.2,
        "grain": 0.25,
        "gloss": 0.42
      }
    },
    {
      "code": "FLOOR-WOOD-WALNUT",
      "applies": "floor",
      "group": "wood",
      "name": "Walnut planks",
      "name_ar": "ألواح جوز",
      "tone": "#634532",
      "roughness": 0.45,
      "size_m": 2.4,
      "paint": {
        "kind": "planks",
        "base": "#6c4c37",
        "width_m": 0.16,
        "grain": 0.3,
        "gloss": 0.38
      }
    },
    {
      "code": "FLOOR-WOOD-HERRINGBONE",
      "applies": "floor",
      "group": "wood",
      "name": "Oak herringbone",
      "name_ar": "باركيه بلوط بنقش عظم السمكة",
      "tone": "#9e7c58",
      "roughness": 0.45,
      "size_m": 0.7,
      "paint": {
        "kind": "herringbone",
        "base": "#b48c63",
        "length": 5
      }
    },
    {
      "code": "FLOOR-CONCRETE-POLISHED",
      "applies": "floor",
      "group": "concrete",
      "name": "Polished concrete",
      "name_ar": "خرسانة مصقولة",
      "tone": "#b1afab",
      "roughness": 0.32,
      "size_m": 3,
      "paint": {
        "kind": "polished",
        "base": "#b6b4b0"
      }
    },
    {
      "code": "FLOOR-CONCRETE",
      "applies": "floor",
      "group": "concrete",
      "name": "Concrete",
      "name_ar": "خرسانة",
      "tone": "#a09e9a",
      "roughness": 0.88,
      "size_m": 4,
      "paint": {
        "kind": "concrete",
        "base": "#a6a4a0"
      }
    },
    {
      "code": "FLOOR-EPOXY-GREY",
      "applies": "floor",
      "group": "concrete",
      "name": "Epoxy, grey",
      "name_ar": "إيبوكسي رمادي",
      "tone": "#8c9195",
      "roughness": 0.22,
      "size_m": 3,
      "paint": {
        "kind": "epoxy",
        "base": "#8c9195"
      }
    },
    {
      "code": "FLOOR-RUBBER",
      "applies": "floor",
      "group": "technical",
      "name": "Rubber, anti-slip studs",
      "name_ar": "مطاط مانع للانزلاق",
      "tone": "#3a3c3f",
      "roughness": 0.85,
      "size_m": 1,
      "paint": {
        "kind": "rubber",
        "base": "#3a3c3f"
      }
    },
    {
      "code": "FLOOR-RAISED-ACCESS",
      "applies": "floor",
      "group": "technical",
      "name": "Raised access floor",
      "name_ar": "أرضية مرفوعة",
      "tone": "#b7b9b8",
      "roughness": 0.45,
      "size_m": 1.2,
      "paint": {
        "kind": "raised",
        "base": "#b9bbba",
        "speckle": [
          "#8c8f91",
          "#d8d9d6"
        ]
      }
    },
    {
      "code": "WALL-PAINT-WHITE",
      "applies": "wall",
      "group": "paint",
      "name": "Paint, white",
      "name_ar": "دهان أبيض",
      "tone": "#eae7e1",
      "roughness": 0.9,
      "size_m": 1,
      "paint": {
        "kind": "paint",
        "base": "#eae7e1"
      }
    },
    {
      "code": "WALL-PAINT-OFFWHITE",
      "applies": "wall",
      "group": "paint",
      "name": "Paint, warm off-white",
      "name_ar": "دهان أبيض دافئ",
      "tone": "#ece3d3",
      "roughness": 0.9,
      "size_m": 1,
      "paint": {
        "kind": "paint",
        "base": "#ece3d3"
      }
    },
    {
      "code": "WALL-PAINT-GREY",
      "applies": "wall",
      "group": "paint",
      "name": "Paint, light grey",
      "name_ar": "دهان رمادي فاتح",
      "tone": "#cfd0cf",
      "roughness": 0.9,
      "size_m": 1,
      "paint": {
        "kind": "paint",
        "base": "#cfd0cf"
      }
    },
    {
      "code": "WALL-PAINT-SAND",
      "applies": "wall",
      "group": "paint",
      "name": "Paint, sand",
      "name_ar": "دهان رملي",
      "tone": "#dccbab",
      "roughness": 0.9,
      "size_m": 1,
      "paint": {
        "kind": "paint",
        "base": "#dccbab"
      }
    },
    {
      "code": "WALL-PAINT-BLUE",
      "applies": "wall",
      "group": "paint",
      "name": "Paint, pale blue",
      "name_ar": "دهان أزرق فاتح",
      "tone": "#c9d8e3",
      "roughness": 0.9,
      "size_m": 1,
      "paint": {
        "kind": "paint",
        "base": "#c9d8e3"
      }
    },
    {
      "code": "WALL-PAINT-GREEN",
      "applies": "wall",
      "group": "paint",
      "name": "Paint, pale green",
      "name_ar": "دهان أخضر فاتح",
      "tone": "#cfdcc8",
      "roughness": 0.9,
      "size_m": 1,
      "paint": {
        "kind": "paint",
        "base": "#cfdcc8"
      }
    },
    {
      "code": "WALL-PAINT-CHARCOAL",
      "applies": "wall",
      "group": "paint",
      "name": "Paint, accent dark grey",
      "name_ar": "دهان رمادي داكن",
      "tone": "#4a4d51",
      "roughness": 0.85,
      "size_m": 1,
      "paint": {
        "kind": "paint",
        "base": "#4a4d51"
      }
    },
    {
      "code": "WALL-PAINT-NAVY",
      "applies": "wall",
      "group": "paint",
      "name": "Paint, accent navy",
      "name_ar": "دهان كحلي",
      "tone": "#2c3d5c",
      "roughness": 0.85,
      "size_m": 1,
      "paint": {
        "kind": "paint",
        "base": "#2c3d5c"
      }
    },
    {
      "code": "WALL-PAINT-TERRACOTTA",
      "applies": "wall",
      "group": "paint",
      "name": "Paint, accent terracotta",
      "name_ar": "دهان تيراكوتا",
      "tone": "#b8694c",
      "roughness": 0.85,
      "size_m": 1,
      "paint": {
        "kind": "paint",
        "base": "#b8694c"
      }
    },
    {
      "code": "WALL-PAPER-LINEN",
      "applies": "wall",
      "group": "wallpaper",
      "name": "Wallpaper, linen beige",
      "name_ar": "ورق جدران، كتان بيج",
      "tone": "#d9cdb7",
      "roughness": 0.85,
      "size_m": 0.5,
      "paint": {
        "kind": "linen",
        "base": "#d8ccb7"
      }
    },
    {
      "code": "WALL-PAPER-STRIPES",
      "applies": "wall",
      "group": "wallpaper",
      "name": "Wallpaper, grey stripes",
      "name_ar": "ورق جدران، خطوط رمادية",
      "tone": "#c8c9c8",
      "roughness": 0.8,
      "size_m": 0.6,
      "paint": {
        "kind": "stripes",
        "base": "#dcdcd9",
        "stripe": "#9c9fa3"
      }
    },
    {
      "code": "WALL-PAPER-GEOMETRIC",
      "applies": "wall",
      "group": "wallpaper",
      "name": "Wallpaper, geometric",
      "name_ar": "ورق جدران، نقش هندسي",
      "tone": "#cfd5d5",
      "roughness": 0.8,
      "size_m": 0.6,
      "paint": {
        "kind": "geometric",
        "base": "#e0e3e2",
        "line": "#7f949b"
      }
    },
    {
      "code": "WALL-PAPER-DAMASK",
      "applies": "wall",
      "group": "wallpaper",
      "name": "Wallpaper, damask",
      "name_ar": "ورق جدران، دمشقي",
      "tone": "#cdc2a9",
      "roughness": 0.75,
      "size_m": 1.2,
      "paint": {
        "kind": "damask",
        "base": "#d4cab3",
        "motif": "#b1a483"
      }
    },
    {
      "code": "WALL-TILE-WHITE",
      "applies": "wall",
      "group": "wall-tiles",
      "name": "Wall tiles 30×60, white",
      "name_ar": "بلاط جدران 30×60، أبيض",
      "tone": "#eff0ee",
      "roughness": 0.15,
      "size_m": 1.2,
      "paint": {
        "kind": "tiles",
        "base": "#f0f1ef",
        "grout": "#c9c9c4",
        "tile_m": [
          0.6,
          0.3
        ],
        "offset": 0,
        "vein": 0.02,
        "gloss": 0.12
      }
    },
    {
      "code": "WALL-TILE-GREY",
      "applies": "wall",
      "group": "wall-tiles",
      "name": "Wall tiles 30×60, grey",
      "name_ar": "بلاط جدران 30×60، رمادي",
      "tone": "#aaacad",
      "roughness": 0.18,
      "size_m": 1.2,
      "paint": {
        "kind": "tiles",
        "base": "#a9abac",
        "grout": "#d8d8d4",
        "tile_m": [
          0.6,
          0.3
        ],
        "offset": 0.5,
        "vein": 0.05,
        "gloss": 0.15
      }
    },
    {
      "code": "WALL-TILE-MOSAIC",
      "applies": "wall",
      "group": "wall-tiles",
      "name": "Mosaic tiles, blue",
      "name_ar": "فسيفساء زرقاء",
      "tone": "#7ca9b9",
      "roughness": 0.15,
      "size_m": 0.5,
      "paint": {
        "kind": "mosaic",
        "colors": [
          "#2f6f8f",
          "#5d9bb8",
          "#a9cfdc",
          "#e4eef0",
          "#3f8a8a"
        ],
        "grout": "#d9dcd8"
      }
    },
    {
      "code": "WALL-WOOD-SLATS",
      "applies": "wall",
      "group": "cladding",
      "name": "Oak slats",
      "name_ar": "شرائح بلوط",
      "tone": "#927557",
      "roughness": 0.55,
      "size_m": 1.2,
      "paint": {
        "kind": "slats",
        "base": "#bf9a72",
        "gap": "#3a2c22"
      }
    },
    {
      "code": "WALL-WOOD-WALNUT",
      "applies": "wall",
      "group": "cladding",
      "name": "Walnut panels",
      "name_ar": "ألواح جوز للجدران",
      "tone": "#684b35",
      "roughness": 0.5,
      "size_m": 1.2,
      "paint": {
        "kind": "panels",
        "base": "#6e4f38"
      }
    },
    {
      "code": "WALL-STONE",
      "applies": "wall",
      "group": "cladding",
      "name": "Stone cladding",
      "name_ar": "تكسية حجرية",
      "tone": "#c5b28d",
      "roughness": 0.9,
      "size_m": 1.2,
      "paint": {
        "kind": "stone",
        "colors": [
          "#d6c6a4",
          "#c9b48d",
          "#dccfb3",
          "#bfa983",
          "#cdbd9b"
        ],
        "joint": "#9c8f78"
      }
    }
  ],
  "defaults": {
    "floor": {
      "office": "FLOOR-CARPET-BLUEGREY",
      "room": "FLOOR-CARPET-BLUEGREY",
      "open_area": "FLOOR-CARPET-BLUEGREY",
      "meeting_room": "FLOOR-CARPET-WARMGREY",
      "prayer_room": "FLOOR-CARPET-GREEN",
      "corridor": "FLOOR-PORCELAIN-GREY",
      "lobby": "FLOOR-MARBLE-BEIGE",
      "elevator": "FLOOR-TERRAZZO-LIGHT",
      "stairs": "FLOOR-TERRAZZO-LIGHT",
      "escalator": "FLOOR-TERRAZZO-LIGHT",
      "ramp": "FLOOR-TERRAZZO-LIGHT",
      "restroom": "FLOOR-PORCELAIN-WHITE",
      "bathroom": "FLOOR-PORCELAIN-WHITE",
      "laundry": "FLOOR-PORCELAIN-WHITE",
      "kitchen": "FLOOR-PORCELAIN-BEIGE",
      "balcony": "FLOOR-PORCELAIN-BEIGE",
      "terrace": "FLOOR-PORCELAIN-BEIGE",
      "bedroom": "FLOOR-WOOD-OAK",
      "living_room": "FLOOR-WOOD-OAK",
      "dining_room": "FLOOR-WOOD-OAK",
      "dressing_room": "FLOOR-WOOD-OAK",
      "storage": "FLOOR-CONCRETE",
      "utility": "FLOOR-CONCRETE",
      "shaft": "FLOOR-CONCRETE",
      "unspecified": "FLOOR-CONCRETE",
      "open_to_below": "FLOOR-CONCRETE",
      "parking": "FLOOR-EPOXY-GREY"
    },
    "wall": {
      "office": "WALL-PAINT-WHITE",
      "room": "WALL-PAINT-WHITE",
      "open_area": "WALL-PAINT-WHITE",
      "meeting_room": "WALL-PAINT-WHITE",
      "prayer_room": "WALL-PAINT-WHITE",
      "corridor": "WALL-PAINT-WHITE",
      "lobby": "WALL-PAINT-WHITE",
      "elevator": "WALL-PAINT-WHITE",
      "stairs": "WALL-PAINT-WHITE",
      "escalator": "WALL-PAINT-WHITE",
      "ramp": "WALL-PAINT-WHITE",
      "restroom": "WALL-TILE-WHITE",
      "bathroom": "WALL-TILE-WHITE",
      "laundry": "WALL-TILE-WHITE",
      "kitchen": "WALL-PAINT-WHITE",
      "balcony": "WALL-PAINT-WHITE",
      "terrace": "WALL-PAINT-WHITE",
      "bedroom": "WALL-PAINT-WHITE",
      "living_room": "WALL-PAINT-WHITE",
      "dining_room": "WALL-PAINT-WHITE",
      "dressing_room": "WALL-PAINT-WHITE",
      "storage": "WALL-PAINT-WHITE",
      "utility": "WALL-PAINT-WHITE",
      "shaft": "WALL-PAINT-WHITE",
      "unspecified": "WALL-PAINT-WHITE",
      "open_to_below": "WALL-PAINT-WHITE",
      "parking": "WALL-PAINT-WHITE"
    }
  }
};

const BY_CODE = new Map(FINISHES.finishes.map((f) => [f.code, f]));

/** A finish by its code ({ code, applies, group, name, name_ar, tone, roughness, size_m,
 * paint }), or null. */
export function finishOf(code) {
  return (typeof code === "string" && BY_CODE.get(code)) || null;
}

/** A finish's code when it is one of this list's, for floors or walls (`applies`); else null. */
export function knownFinish(code, applies) {
  const f = finishOf(code);
  return f && f.applies === applies ? f.code : null;
}

/** The finish a type of room has when it is given none (an unknown type: as `unspecified`). */
export function defaultFinish(applies, type) {
  const table = FINISHES.defaults[applies];
  return table[type] ?? table.unspecified;
}

/** The floor finish a space or zone shows, from its properties (and, for a zone, its
 * space's): its own, else its space's, else its type's default. */
export function floorFinish(props, space = null) {
  return knownFinish(props?.floor_finish, "floor") ?? knownFinish(space?.floor_finish, "floor")
    ?? defaultFinish("floor", props?.type);
}

/** The finish of a space's walls, from its properties: its own, else its type's default. */
export function wallFinish(props) {
  return knownFinish(props?.wall_finish, "wall") ?? defaultFinish("wall", props?.type);
}

/** The finish of walls' faces outside every room. */
export const EXTERIOR = FINISHES.exterior;
