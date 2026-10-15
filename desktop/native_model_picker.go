package main

import (
	"context"

	"github.com/egoist/mygo/ui"
)

// modelPicker chooses a saved model for one extraction or drafting run; the
// empty value leaves the choice to the server's default model.
type modelPicker struct {
	value  string
	codes  []string
	labels map[string]string
	loaded bool
}

func (m *modelPicker) load(a *NativeApp) {
	if m.loaded {
		return
	}
	m.loaded = true
	a.Do(func(ctx context.Context) (func(), error) {
		r, err := a.Client.Request(ctx, "GET", "/management/providers", nil)
		// Without the list the run still uses the default model, so a failed read only hides the picker.
		if err != nil || !settingsProviderSettings(r.Data) {
			return nil, nil
		}
		return func() {
			m.codes, m.labels = []string{""}, map[string]string{"": "默认模型"}
			for _, model := range objects(r.Data["models"]) {
				label := modelTitle(model) + " · " + str(model, "model")
				if str(model, "slot") == str(r.Data, "default_slot") {
					m.labels[""] = "默认：" + label
					continue
				}
				m.codes = append(m.codes, str(model, "slot"))
				m.labels[str(model, "slot")] = label
			}
			if _, ok := m.labels[m.value]; !ok {
				m.value = ""
			}
		}, nil
	})
}

// View shows the picker only when there is a choice to make and reports a change.
func (m *modelPicker) View(c *ui.Context) bool {
	if len(m.codes) < 2 {
		return false
	}
	changed := false
	ui.Column(c.Key("model-picker")).Gap(6).Children(func() {
		ui.Text(c, "模型").FontSize(12.5).FontWeight(500).TextColor(nativePalette(c).Muted)
		changed = nativeChoice(c, &m.value, m.codes, m.labels).Label("本次使用的模型").MinWidth(220).Changed()
	})
	return changed
}

func (m *modelPicker) set(body Object) {
	if m.value != "" {
		body["model"] = m.value
	}
}
