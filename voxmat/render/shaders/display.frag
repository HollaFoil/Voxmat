#version 330
// Display pass: average the accumulation buffer, apply exposure, tone map and
// gamma. Standalone (no scene access) so it does not include common.glsl.
out vec4 f_color;
in vec2 v_uv;
uniform sampler2D u_accum;
uniform int   u_samples;
uniform float u_exposure;
uniform int   u_tonemap;     // 0 filmic, 1 aces, 2 reinhard, 3 linear

vec3 aces(vec3 x) {
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}
// Filmic with a lifted toe (gentler shadows than ACES).
vec3 filmic(vec3 x) {
    x = max(vec3(0.0), x);
    vec3 r = (x * (x * 0.22 + 0.08)) / (x * (x * 0.22 + 0.30) + 0.06);
    return clamp(r, 0.0, 1.0);
}
vec3 reinhard(vec3 x) { return x / (1.0 + x); }

void main() {
    vec3 c = texture(u_accum, v_uv).rgb / float(max(u_samples, 1));
    c *= u_exposure;
    if      (u_tonemap == 0) c = filmic(c);
    else if (u_tonemap == 1) c = aces(c);
    else if (u_tonemap == 2) c = reinhard(c);
    // 3 = linear (no curve)
    c = pow(clamp(c, 0.0, 1.0), vec3(1.0 / 2.2));
    f_color = vec4(c, 1.0);
}
