package main

// A vendor template only pre-fills the model form; every field stays editable
// and nothing is saved until the user enters a key and saves. Model IDs drift,
// so the template model is a starting point the vendor console can override.
type modelTemplate struct {
	Key, Label, Name, Provider, Endpoint, Model, JSONMode, Default, Note string
	Levels                                                               []modelReasoningEditor
}

// Extraction ran fastest with short batches of 4000 characters (see docs/notes/llm-providers.md).
func templateLevel(name, label, options string) modelReasoningEditor {
	return modelReasoningEditor{Name: name, Label: label, Options: options, Batch: "4000"}
}

var modelTemplates = []modelTemplate{
	{Key: "zhipu", Label: "智谱 GLM", Name: "智谱 GLM", Provider: "openai", Endpoint: "https://open.bigmodel.cn/api/paas/v4", Model: "glm-5.3-flash", JSONMode: "json_object", Default: "low",
		Levels: []modelReasoningEditor{
			templateLevel("low", "轻量思考（推荐）", `{"reasoning_effort":"low","thinking":{"type":"enabled"}}`),
			templateLevel("high", "深度思考", `{"reasoning_effort":"high","thinking":{"type":"enabled"}}`),
			templateLevel("off", "关闭思考", `{"thinking":{"type":"disabled"}}`),
		}},
	{Key: "deepseek", Label: "DeepSeek", Name: "DeepSeek", Provider: "openai", Endpoint: "https://api.deepseek.com", Model: "deepseek-v4-flash", JSONMode: "json_object", Default: "off",
		Levels: []modelReasoningEditor{
			templateLevel("off", "关闭思考（推荐）", `{"thinking":{"type":"disabled"}}`),
			templateLevel("high", "深度思考", `{"thinking":{"type":"enabled"},"reasoning_effort":"high"}`),
		}},
	{Key: "qwen", Label: "通义千问（阿里云百炼）", Name: "通义千问", Provider: "openai", Endpoint: "https://dashscope.aliyuncs.com/compatible-mode/v1", Model: "qwen-plus", JSONMode: "json_object", Default: "off",
		Note: "百炼新版业务空间可能使用带空间 ID 的地址，以控制台显示为准。",
		Levels: []modelReasoningEditor{
			templateLevel("off", "关闭思考（推荐）", `{"enable_thinking":false}`),
			templateLevel("on", "开启思考", `{"enable_thinking":true}`),
		}},
	{Key: "kimi", Label: "Kimi（月之暗面）", Name: "Kimi", Provider: "openai", Endpoint: "https://api.moonshot.cn/v1", Model: "kimi-k3", JSONMode: "json_schema", Default: "low",
		Note: "Kimi K3 始终思考，只能调节强度；国际站地址为 https://api.moonshot.ai/v1。",
		Levels: []modelReasoningEditor{
			templateLevel("low", "轻量思考（推荐）", `{"reasoning_effort":"low"}`),
			templateLevel("high", "深度思考", `{"reasoning_effort":"high"}`),
		}},
	{Key: "minimax", Label: "MiniMax", Name: "MiniMax", Provider: "openai", Endpoint: "https://api.minimaxi.com/v1", Model: "MiniMax-M3", JSONMode: "json_object",
		Note: "国际站地址为 https://api.minimax.io/v1。"},
	{Key: "siliconflow", Label: "硅基流动", Name: "硅基流动", Provider: "openai", Endpoint: "https://api.siliconflow.cn/v1", Model: "deepseek-ai/DeepSeek-V3", JSONMode: "json_object",
		Note: "硅基流动托管多家开源模型，模型名称按控制台「模型广场」填写。"},
	{Key: "openrouter", Label: "OpenRouter", Name: "OpenRouter", Provider: "openai", Endpoint: "https://openrouter.ai/api/v1", Model: "deepseek/deepseek-v4-flash", JSONMode: "json_object", Default: "low",
		Levels: []modelReasoningEditor{
			templateLevel("low", "轻量思考（推荐）", `{"reasoning":{"effort":"low"}}`),
			templateLevel("high", "深度思考", `{"reasoning":{"effort":"high"}}`),
		}},
	{Key: "anthropic", Label: "Anthropic Claude", Name: "Claude", Provider: "anthropic", Model: "claude-sonnet-5-5", JSONMode: "json_schema", Default: "low",
		Levels: []modelReasoningEditor{
			{Name: "low", Label: "轻量推理（推荐）", Options: "{}", Effort: "low", Batch: "4000"},
			{Name: "high", Label: "深度推理", Options: "{}", Effort: "high", Batch: "4000"},
		}},
}

func modelTemplateOptions() settingsChoices {
	pairs := []string{"", "自定义（手动填写）"}
	for _, template := range modelTemplates {
		pairs = append(pairs, template.Key, template.Label)
	}
	return settingsOptions(pairs...)
}

func findModelTemplate(key string) (modelTemplate, bool) {
	for _, template := range modelTemplates {
		if template.Key == key {
			return template, true
		}
	}
	return modelTemplate{}, false
}
