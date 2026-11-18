package org.maibot.androidpet;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

final class J {
    static JSONObject obj(Object... pairs) {
        JSONObject result = new JSONObject();
        try { for (int i = 0; i < pairs.length; i += 2) result.put((String) pairs[i], pairs[i + 1] == null ? JSONObject.NULL : pairs[i + 1]); }
        catch (JSONException error) { throw new IllegalArgumentException(error); }
        return result;
    }
    static JSONArray arr(Object... items) { JSONArray result = new JSONArray(); for (Object item : items) result.put(item == null ? JSONObject.NULL : item); return result; }
    static String id() { return java.util.UUID.randomUUID().toString().replace("-", ""); }
}
