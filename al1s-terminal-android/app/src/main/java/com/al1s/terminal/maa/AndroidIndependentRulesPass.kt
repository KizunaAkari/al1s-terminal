package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

object AndroidIndependentRulesPass {
    fun apply(compiled:AndroidCompiledModule,script:JSONObject,materialize:(String)->String) {
        val rules=script.optJSONArray("global_popups") ?: return
        require(rules.length()<=50)
        val candidates=mutableMapOf<Int,MutableList<JSONObject>>()
        for(ruleIndex in 0 until rules.length()) {
            val rule=rules.getJSONObject(ruleIndex);if(rule.opt("enabled")==false)continue
            val selected=rule.optJSONArray("step_indexes")?.let { values->(0 until values.length()).map { values.getInt(it)-1 } } ?:
                ((rule.optInt("from_step_index",1)-1)..(rule.optInt("through_step_index",compiled.stepNodes.size)-1)).toList()
            require(selected.isNotEmpty() && selected.all { it in compiled.stepNodes.keys })
            selected.distinct().forEach { step ->
                val key="Global_${ruleIndex}_ForStep_$step"
                val mode=rule.optString("click_mode","fixed")
                val config=JSONObject(rule.toString()).put("action",if(mode=="image")"wait_image" else "wait_click")
                val node=AndroidStepNodes.build(config,step,materialize)
                node.put("post_delay",AndroidPipelineCompiler.milliseconds(rule.optDouble("wait_after_click_seconds",0.3)))
                node.put("attach",JSONObject().put("dsl_step_index",step).put("dsl_action","independent_rule")
                    .put("maa_project_role","global-popup").put("popup_key",key).put("popup_condition",true)
                    .put("popup_budget",rule.optDouble("timeout_seconds",30.0)).put("rule_index",ruleIndex))
                if(mode=="image") {
                    val clickConfig=JSONObject(config.toString()).put("action","wait_click").put("click_mode","match_center")
                        .put("template_base64",rule.getString("click_template_base64"))
                        .put("threshold",rule.optDouble("click_threshold",rule.optDouble("threshold",0.85)))
                    clickConfig.remove("template")
                    val clickName="${key}_Click";val click=AndroidStepNodes.build(clickConfig,step,materialize)
                    click.put("attach",JSONObject(node.getJSONObject("attach").toString()).put("popup_condition",false))
                        .put("post_delay",node.getInt("post_delay"))
                    node.put("next",JSONArray().put(clickName)).put("timeout",3000).put("rate_limit",200)
                    compiled.pipeline.put(clickName,click)
                    guardAction(compiled.pipeline,clickName,click,key)
                } else guardAction(compiled.pipeline,key,node,key)
                compiled.pipeline.put(key,node)
                guardRecognition(compiled.pipeline,key,node,key)
                candidates.getOrPut(step) { mutableListOf() }+=JSONObject().put("name",key).put("jump_back",true)
            }
        }
        val keys=compiled.pipeline.keys().asSequence().toList()
        keys.forEach { name->
            if(name.startsWith("Global_") || name.endsWith("Source"))return@forEach
            val node=compiled.pipeline.getJSONObject(name);val next=node.optJSONArray("next") ?: return@forEach
            val step=(0 until next.length()).mapNotNull { i->
                val value=next.get(i);val target=if(value is String)value else (value as? JSONObject)?.optString("name")
                compiled.pipeline.optJSONObject(target.orEmpty())?.optJSONObject("attach")?.opt("dsl_step_index") as? Int
            }.firstOrNull() ?: return@forEach
            val rulesForStep=candidates[step] ?: return@forEach
            val changed=JSONArray(rulesForStep);for(i in 0 until next.length())changed.put(next.get(i))
            node.put("next",changed)
        }
    }

    private fun guardRecognition(pipeline:JSONObject,name:String,node:JSONObject,key:String) {
        val source="${name}_PopupRecognitionSource"
        pipeline.put(source,JSONObject(node.toString()).put("action","DoNothing").put("next",JSONArray()))
        node.put("recognition","Custom").put("custom_recognition","Al1sPopupLimitRecognition")
            .put("custom_recognition_param",JSONObject().put("source",source).put("key",key))
    }
    private fun guardAction(pipeline:JSONObject,name:String,node:JSONObject,key:String) {
        val source="${name}_PopupActionSource"
        pipeline.put(source,JSONObject(node.toString()).put("pre_delay",0).put("post_delay",0).put("next",JSONArray()).put("repeat",1))
        node.put("action","Custom").put("custom_action","Al1sPopupCountClick")
            .put("custom_action_param",JSONObject().put("source",source).put("key",key))
    }
}
