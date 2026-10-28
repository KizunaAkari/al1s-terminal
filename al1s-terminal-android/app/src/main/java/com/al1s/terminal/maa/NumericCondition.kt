package com.al1s.terminal.maa

object NumericCondition {
    fun firstNumber(texts: List<String>): Double? {
        val pattern = Regex("[-+]?(?:\\d[\\d,.]*\\d|\\d|[.,]\\d+)")
        for (text in texts) {
            var token = pattern.find(text.replace(" ", ""))?.value ?: continue
            if (',' in token && '.' in token) token = token.replace(",", "")
            else if (',' in token) {
                val parts = token.trimStart('+', '-').split(',')
                token = if (parts.size > 2 || parts.last().length == 3) token.replace(",", "") else token.replace(',', '.')
            }
            token.toDoubleOrNull()?.takeIf { it.isFinite() }?.let { return it }
        }
        return null
    }
    fun matches(number: Double?, operator: String, threshold: Double): Boolean {
        require(operator in setOf("gt", "lt") && threshold.isFinite())
        return number?.let { if (operator == "gt") it > threshold else it < threshold } ?: false
    }
}
