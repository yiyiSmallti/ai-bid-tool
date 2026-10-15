package main

import (
	"embed"
	"strings"

	"github.com/egoist/mygo/ui"
)

// Lucide icons (ISC, see icons/LICENSE), drawn in the text color.
//
//go:embed icons/*.svg
var nativeIconFiles embed.FS

var nativeIcons = func() map[string]*ui.SVG {
	icons := map[string]*ui.SVG{}
	entries, _ := nativeIconFiles.ReadDir("icons")
	for _, entry := range entries {
		data, err := nativeIconFiles.ReadFile("icons/" + entry.Name())
		if err != nil {
			panic(err)
		}
		icons[strings.TrimSuffix(entry.Name(), ".svg")] = ui.MustParseSVG(data)
	}
	return icons
}()

// nativeIcon panics on a missing name so a typo fails the first render.
func nativeIcon(name string) *ui.SVG {
	icon, ok := nativeIcons[name]
	if !ok {
		panic("unknown icon " + name)
	}
	return icon
}
