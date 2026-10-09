package com.al1s.terminal.maa

import org.json.JSONObject

object HandlerRegistry {
    private val recognitions = setOf("DirectHit", "TemplateMatch", "FeatureMatch", "ColorMatch", "OCR", "NeuralNetworkClassify", "NeuralNetworkDetect", "And", "Or")
    private val actions = setOf("DoNothing", "Click", "LongPress", "Swipe", "MultiSwipe", "TouchDown", "TouchMove", "TouchUp",
        "ClickKey", "LongPressKey", "KeyDown", "KeyUp", "InputText", "StartApp", "StopApp")

    fun requireAll(pipeline: JSONObject, registeredActions: Set<String> = emptySet(), registeredRecognitions: Set<String> = emptySet()) {
        require(pipeline.length() in 1..10000) { "pipeline_size_invalid" }
        pipeline.keys().forEach { name ->
            val node = pipeline.getJSONObject(name)
            val recognition = node.optString("recognition", "DirectHit")
            require(recognition in recognitions || recognition == "Custom" &&
                node.optString("custom_recognition") in registeredRecognitions) { "recognition_handler_unavailable" }
            val action = node.optString("action", "DoNothing")
            require(action in actions || action == "Custom" && node.optString("custom_action") in registeredActions) { "action_handler_unavailable" }
            require(!node.has("exec") && !node.has("command")) { "command_action_rejected" }
        }
    }
}
