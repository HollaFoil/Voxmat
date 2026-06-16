#version 330
// Edge-avoiding À-Trous wavelet denoise pass, guided by the albedo + normal
// G-buffers. Standalone (no scene access) so it does not include common.glsl.
out vec4 f_color;
in vec2 v_uv;
uniform sampler2D u_in, u_albedo, u_normal;
uniform ivec2 u_size;
uniform int   u_step;
uniform bool  u_is_accum;    // input is the summed accum buffer -> divide by samples
uniform int   u_samples;
uniform float u_c_phi, u_n_phi, u_a_phi;

vec3 col(ivec2 p) {
    vec3 c = texelFetch(u_in, p, 0).rgb;
    return u_is_accum ? c / float(max(u_samples, 1)) : c;
}
float luminance(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    vec3 colorP = col(p);
    vec3 normalP = texelFetch(u_normal, p, 0).rgb * 2.0 - 1.0;
    vec3 albedoP = texelFetch(u_albedo, p, 0).rgb;
    float lumP = luminance(colorP);
    // Scale-invariant colour edge-stop, evaluated in luminance and normalised by
    // the *centre* brightness. Using raw HDR rgb differences here (the old code)
    // made a firefly look like a hard edge — its neighbours got ~zero weight and
    // the spike was preserved. Normalising by lumP^2 makes the denominator large
    // exactly at a bright pixel, so a firefly blends with its dim neighbours and
    // is smoothed away, while dim pixels still refuse a bright neighbour (no halo).
    float colorScale = u_c_phi * (lumP * lumP + 1e-2);
    float kernel[5] = float[5](0.0625, 0.25, 0.375, 0.25, 0.0625);
    vec3 sum = vec3(0.0); float weightSum = 0.0;
    for (int dy = -2; dy <= 2; dy++)
    for (int dx = -2; dx <= 2; dx++) {
        ivec2 q = clamp(p + ivec2(dx, dy) * u_step, ivec2(0), u_size - 1);
        vec3 colorQ = col(q);
        vec3 normalQ = texelFetch(u_normal, q, 0).rgb * 2.0 - 1.0;
        vec3 albedoQ = texelFetch(u_albedo, q, 0).rgb;
        float w = kernel[dx + 2] * kernel[dy + 2];
        if (dx != 0 || dy != 0) {     // centre tap always counts (keeps bg/flat areas)
            float wn = pow(max(dot(normalP, normalQ), 0.0), u_n_phi);
            vec3 da = albedoP - albedoQ; float wa = exp(-dot(da, da) / u_a_phi);
            float dl = lumP - luminance(colorQ);
            float wc = exp(-(dl * dl) / colorScale);
            w *= wn * wa * wc;
        }
        sum += colorQ * w; weightSum += w;
    }
    f_color = vec4(sum / max(weightSum, 1e-4), 1.0);
}
