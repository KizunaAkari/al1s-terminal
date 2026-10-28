package com.al1s.terminal.maa

import org.json.JSONArray
import org.json.JSONObject

object PipelineNamespace {
    fun rename(compiled:AndroidCompiledModule,prefix:String):AndroidCompiledModule {
        val names=compiled.pipeline.keys().asSequence().associateWith { prefix+it }
        fun value(item:Any?,key:String):Any? = when(item) {
            is JSONObject -> JSONObject().also { result -> item.keys().forEach { child->
                result.put(if(key=="pipeline")names[child] ?: child else child,value(item.get(child),child))
            } }
            is JSONArray -> JSONArray((0 until item.length()).map { i->value(item.get(i),key) })
            is String -> if(key in setOf("next","on_error","source","entry","name","reset_hit_count_nodes","retry_entry","target_node"))names[item] ?: item else item
            else -> item
        }
        val pipeline=JSONObject()
        compiled.pipeline.keys().forEach { pipeline.put(names.getValue(it),value(compiled.pipeline.get(it),"node")) }
        return AndroidCompiledModule(names.getValue(compiled.entry),pipeline,compiled.stepNodes.mapValues { names.getValue(it.value) })
    }
}
