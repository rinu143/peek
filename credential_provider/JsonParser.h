// Peek Face Engine - Strict JSON and IPC Schema Parser
#pragma once

#include <string>
#include <vector>
#include <map>
#include <sstream>
#include <cctype>
#include <memory>
#include <algorithm>
#include <windows.h>

namespace PeekJson
{
    enum class JsonType
    {
        Null,
        Bool,
        Number,
        String,
        Array,
        Object
    };

    static inline std::wstring Utf8ToWide(const std::string& utf8Str)
    {
        if (utf8Str.empty()) return L"";
        int cchNeeded = MultiByteToWideChar(CP_UTF8, 0, utf8Str.c_str(), (int)utf8Str.size(), nullptr, 0);
        if (cchNeeded <= 0) return L"";
        std::wstring wideStr(cchNeeded, 0);
        MultiByteToWideChar(CP_UTF8, 0, utf8Str.c_str(), (int)utf8Str.size(), &wideStr[0], cchNeeded);
        return wideStr;
    }

    class JsonValue
    {
    public:
        JsonType type = JsonType::Null;
        bool boolVal = false;
        double numVal = 0.0;
        std::string strVal;
        std::vector<JsonValue> arrVal;
        std::map<std::string, JsonValue> objVal;

        bool IsObject() const { return type == JsonType::Object; }
        bool IsString() const { return type == JsonType::String; }
        bool IsBool() const { return type == JsonType::Bool; }
        bool IsNumber() const { return type == JsonType::Number; }

        const JsonValue* Get(const std::string& key) const
        {
            if (type != JsonType::Object) return nullptr;
            auto it = objVal.find(key);
            if (it != objVal.end()) return &it->second;
            return nullptr;
        }

        bool HasKey(const std::string& key) const
        {
            if (type != JsonType::Object) return false;
            return objVal.find(key) != objVal.end();
        }

        bool GetString(const std::string& key, std::wstring& outVal) const
        {
            const JsonValue* v = Get(key);
            if (!v || v->type != JsonType::String) return false;
            outVal = Utf8ToWide(v->strVal);
            return true;
        }

        bool GetBool(const std::string& key, bool& outVal) const
        {
            const JsonValue* v = Get(key);
            if (!v || v->type != JsonType::Bool) return false;
            outVal = v->boolVal;
            return true;
        }

        bool GetDouble(const std::string& key, double& outVal) const
        {
            const JsonValue* v = Get(key);
            if (!v || v->type != JsonType::Number) return false;
            outVal = v->numVal;
            return true;
        }
    };

    class StrictJsonParser
    {
    public:
        static bool Parse(const std::string& input, JsonValue& outValue, std::string& outError)
        {
            size_t idx = 0;
            SkipWhitespace(input, idx);
            if (idx >= input.size())
            {
                outError = "Empty input";
                return false;
            }

            if (!ParseValue(input, idx, outValue, outError))
            {
                return false;
            }

            SkipWhitespace(input, idx);
            if (idx < input.size())
            {
                outError = "Trailing garbage after JSON value at position " + std::to_string(idx);
                return false;
            }

            return true;
        }

    private:
        static void SkipWhitespace(const std::string& s, size_t& idx)
        {
            while (idx < s.size() && (s[idx] == ' ' || s[idx] == '\t' || s[idx] == '\r' || s[idx] == '\n'))
            {
                idx++;
            }
        }

        static bool ParseValue(const std::string& s, size_t& idx, JsonValue& v, std::string& err)
        {
            SkipWhitespace(s, idx);
            if (idx >= s.size())
            {
                err = "Unexpected end of input";
                return false;
            }

            char c = s[idx];
            if (c == '{')
            {
                return ParseObject(s, idx, v, err);
            }
            else if (c == '[')
            {
                return ParseArray(s, idx, v, err);
            }
            else if (c == '\"')
            {
                return ParseString(s, idx, v, err);
            }
            else if (c == 't' || c == 'f')
            {
                return ParseBool(s, idx, v, err);
            }
            else if (c == 'n')
            {
                return ParseNull(s, idx, v, err);
            }
            else if (c == '-' || (c >= '0' && c <= '9'))
            {
                return ParseNumber(s, idx, v, err);
            }

            err = std::string("Unexpected character '") + c + "' at position " + std::to_string(idx);
            return false;
        }

        static bool ParseObject(const std::string& s, size_t& idx, JsonValue& v, std::string& err)
        {
            idx++; // consume '{'
            v.type = JsonType::Object;
            v.objVal.clear();

            SkipWhitespace(s, idx);
            if (idx < s.size() && s[idx] == '}')
            {
                idx++;
                return true;
            }

            while (idx < s.size())
            {
                SkipWhitespace(s, idx);
                if (idx >= s.size() || s[idx] != '\"')
                {
                    err = "Expected string key in object at position " + std::to_string(idx);
                    return false;
                }

                JsonValue keyVal;
                if (!ParseString(s, idx, keyVal, err))
                {
                    return false;
                }

                SkipWhitespace(s, idx);
                if (idx >= s.size() || s[idx] != ':')
                {
                    err = "Expected ':' after key at position " + std::to_string(idx);
                    return false;
                }
                idx++; // consume ':'

                JsonValue val;
                if (!ParseValue(s, idx, val, err))
                {
                    return false;
                }

                v.objVal[keyVal.strVal] = val;

                SkipWhitespace(s, idx);
                if (idx >= s.size())
                {
                    err = "Unclosed object";
                    return false;
                }

                if (s[idx] == '}')
                {
                    idx++;
                    return true;
                }
                else if (s[idx] == ',')
                {
                    idx++;
                    SkipWhitespace(s, idx);
                    // Disallow trailing comma
                    if (idx < s.size() && s[idx] == '}')
                    {
                        err = "Trailing comma in object at position " + std::to_string(idx);
                        return false;
                    }
                }
                else
                {
                    err = "Expected ',' or '}' in object at position " + std::to_string(idx);
                    return false;
                }
            }

            err = "Unclosed object";
            return false;
        }

        static bool ParseArray(const std::string& s, size_t& idx, JsonValue& v, std::string& err)
        {
            idx++; // consume '['
            v.type = JsonType::Array;
            v.arrVal.clear();

            SkipWhitespace(s, idx);
            if (idx < s.size() && s[idx] == ']')
            {
                idx++;
                return true;
            }

            while (idx < s.size())
            {
                JsonValue elem;
                if (!ParseValue(s, idx, elem, err))
                {
                    return false;
                }
                v.arrVal.push_back(elem);

                SkipWhitespace(s, idx);
                if (idx >= s.size())
                {
                    err = "Unclosed array";
                    return false;
                }

                if (s[idx] == ']')
                {
                    idx++;
                    return true;
                }
                else if (s[idx] == ',')
                {
                    idx++;
                    SkipWhitespace(s, idx);
                    if (idx < s.size() && s[idx] == ']')
                    {
                        err = "Trailing comma in array at position " + std::to_string(idx);
                        return false;
                    }
                }
                else
                {
                    err = "Expected ',' or ']' in array at position " + std::to_string(idx);
                    return false;
                }
            }

            err = "Unclosed array";
            return false;
        }

        static bool ParseString(const std::string& s, size_t& idx, JsonValue& v, std::string& err)
        {
            idx++; // consume opening '\"'
            v.type = JsonType::String;
            v.strVal.clear();

            while (idx < s.size())
            {
                char c = s[idx++];
                if (c == '\"')
                {
                    return true;
                }
                else if (c == '\\')
                {
                    if (idx >= s.size())
                    {
                        err = "Incomplete escape sequence in string";
                        return false;
                    }
                    char esc = s[idx++];
                    switch (esc)
                    {
                    case '\"': v.strVal += '\"'; break;
                    case '\\': v.strVal += '\\'; break;
                    case '/':  v.strVal += '/';  break;
                    case 'b':  v.strVal += '\b'; break;
                    case 'f':  v.strVal += '\f'; break;
                    case 'n':  v.strVal += '\n'; break;
                    case 'r':  v.strVal += '\r'; break;
                    case 't':  v.strVal += '\t'; break;
                    case 'u':
                        if (idx + 4 > s.size())
                        {
                            err = "Incomplete \\u hex escape in string";
                            return false;
                        }
                        // For basic ASCII hex decoding
                        idx += 4;
                        v.strVal += '?';
                        break;
                    default:
                        err = std::string("Invalid escape sequence \\") + esc;
                        return false;
                    }
                }
                else if (static_cast<unsigned char>(c) < 0x20)
                {
                    err = "Unescaped control character in string";
                    return false;
                }
                else
                {
                    v.strVal += c;
                }
            }

            err = "Unclosed string literal";
            return false;
        }

        static bool ParseBool(const std::string& s, size_t& idx, JsonValue& v, std::string& err)
        {
            if (s.compare(idx, 4, "true") == 0)
            {
                v.type = JsonType::Bool;
                v.boolVal = true;
                idx += 4;
                return true;
            }
            if (s.compare(idx, 5, "false") == 0)
            {
                v.type = JsonType::Bool;
                v.boolVal = false;
                idx += 5;
                return true;
            }
            err = "Invalid boolean literal at position " + std::to_string(idx);
            return false;
        }

        static bool ParseNull(const std::string& s, size_t& idx, JsonValue& v, std::string& err)
        {
            if (s.compare(idx, 4, "null") == 0)
            {
                v.type = JsonType::Null;
                idx += 4;
                return true;
            }
            err = "Invalid null literal at position " + std::to_string(idx);
            return false;
        }

        static bool ParseNumber(const std::string& s, size_t& idx, JsonValue& v, std::string& err)
        {
            size_t start = idx;
            if (s[idx] == '-') idx++;

            if (idx >= s.size() || !std::isdigit(static_cast<unsigned char>(s[idx])))
            {
                err = "Invalid number format at position " + std::to_string(idx);
                return false;
            }

            while (idx < s.size() && std::isdigit(static_cast<unsigned char>(s[idx])))
            {
                idx++;
            }

            if (idx < s.size() && s[idx] == '.')
            {
                idx++;
                if (idx >= s.size() || !std::isdigit(static_cast<unsigned char>(s[idx])))
                {
                    err = "Invalid fraction in number at position " + std::to_string(idx);
                    return false;
                }
                while (idx < s.size() && std::isdigit(static_cast<unsigned char>(s[idx])))
                {
                    idx++;
                }
            }

            if (idx < s.size() && (s[idx] == 'e' || s[idx] == 'E'))
            {
                idx++;
                if (idx < s.size() && (s[idx] == '+' || s[idx] == '-')) idx++;
                if (idx >= s.size() || !std::isdigit(static_cast<unsigned char>(s[idx])))
                {
                    err = "Invalid exponent in number at position " + std::to_string(idx);
                    return false;
                }
                while (idx < s.size() && std::isdigit(static_cast<unsigned char>(s[idx])))
                {
                    idx++;
                }
            }

            std::string numStr = s.substr(start, idx - start);
            try
            {
                v.type = JsonType::Number;
                v.numVal = std::stod(numStr);
                return true;
            }
            catch (...)
            {
                err = "Number out of range: " + numStr;
                return false;
            }
        }
    };

    struct PeekProtocolMessage
    {
        std::wstring type;
        std::wstring protocolVersion;
        std::wstring nonce;
        std::wstring displayName;
        std::wstring detail;
        std::wstring reasonCode;
        std::wstring promptText;
        bool isAuthorizedToUnlock = false;
        double timestamp = 0.0;
        bool isValid = false;
        std::wstring parseError;
    };

    class ProtocolSchemaValidator
    {
    public:
        static bool ValidateAndParse(
            const std::string& line,
            PeekProtocolMessage& outMsg
        )
        {
            outMsg = PeekProtocolMessage();

            JsonValue root;
            std::string err;
            if (!StrictJsonParser::Parse(line, root, err))
            {
                outMsg.isValid = false;
                outMsg.parseError = Utf8ToWide("Malformed JSON: " + err);
                return false;
            }

            if (!root.IsObject())
            {
                outMsg.isValid = false;
                outMsg.parseError = L"JSON root is not an object";
                return false;
            }

            // 1. Prohibited biometric keys check on all messages
            static const std::vector<std::string> prohibitedKeys = {
                "embedding", "embeddings", "template", "templates"
            };
            for (const auto& pk : prohibitedKeys)
            {
                if (root.HasKey(pk))
                {
                    outMsg.isValid = false;
                    outMsg.parseError = Utf8ToWide("Security violation: Prohibited key '" + pk + "' received in message");
                    return false;
                }
            }

            // 2. Validate protocol_version
            if (!root.GetString("protocol_version", outMsg.protocolVersion))
            {
                outMsg.isValid = false;
                outMsg.parseError = L"Missing or invalid 'protocol_version'";
                return false;
            }
            if (outMsg.protocolVersion != L"2.0")
            {
                outMsg.isValid = false;
                outMsg.parseError = L"Unsupported protocol version: " + outMsg.protocolVersion + L"; expected 2.0";
                return false;
            }

            // 3. Validate type
            if (!root.GetString("type", outMsg.type) || outMsg.type.empty())
            {
                outMsg.isValid = false;
                outMsg.parseError = L"Missing or invalid 'type'";
                return false;
            }

            // 4. Extract optional generic fields
            root.GetString("detail", outMsg.detail);
            root.GetString("display_name", outMsg.displayName);
            root.GetString("reason_code", outMsg.reasonCode);
            root.GetString("prompt_text", outMsg.promptText);
            root.GetString("nonce", outMsg.nonce);
            root.GetBool("is_authorized_to_unlock", outMsg.isAuthorizedToUnlock);
            root.GetDouble("timestamp", outMsg.timestamp);

            // 5. Strict schema enforcement for AUTHENTICATED messages
            if (outMsg.type == L"AUTHENTICATED")
            {
                // CRITICAL SECURITY CONSTRAINT: profile_id must NOT be present in AUTHENTICATED
                if (root.HasKey("profile_id"))
                {
                    outMsg.isValid = false;
                    outMsg.parseError = L"Security violation: 'profile_id' received in AUTHENTICATED message";
                    return false;
                }

                // is_authorized_to_unlock must be true
                if (!outMsg.isAuthorizedToUnlock)
                {
                    outMsg.isValid = false;
                    outMsg.parseError = L"AUTHENTICATED message has is_authorized_to_unlock != true";
                    return false;
                }

                // nonce must be present and non-empty (at least 16 hex chars)
                if (outMsg.nonce.empty() || outMsg.nonce.length() < 16)
                {
                    outMsg.isValid = false;
                    outMsg.parseError = L"AUTHENTICATED message missing or invalid challenge nonce";
                    return false;
                }
            }

            outMsg.isValid = true;
            return true;
        }
    };
}
