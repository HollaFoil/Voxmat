#version 330
// Camera pass: primary visibility, diffuse GI from the cache, a multi-bounce
// GGX specular path, and dielectric (glass) transmission. Writes the colour plus
// albedo/normal G-buffers used by the denoiser.
layout(location = 0) out vec4 f_color;
layout(location = 1) out vec4 f_albedo;   // primary-hit albedo (denoiser guide)
layout(location = 2) out vec4 f_normal;   // primary-hit normal  (denoiser guide)
in vec2 v_uv;
uniform sampler2D u_accum;
uniform vec3  u_eye, u_forward, u_right, u_up;
uniform float u_tan_half_fov, u_aspect;
uniform int   u_frame;
uniform int   u_max_bounces;
uniform vec2  u_resolution;
#include "common.glsl"

// Trace through one homogeneous glass block. Caller has decided to transmit:
// this refracts in at the entry interface, marches through the block (treating a
// connected run of one material as a single optical block — one entry + one exit
// refraction, not one per voxel), applies Beer-Lambert absorption over the real
// path length, and refracts out, looping on total internal reflection. On return
// `rayOrigin`/`rayDir` describe the ray leaving the block (into air, or just past
// a differing solid behind it) and `throughput` is attenuated.
//   returns true  -> ray continues; caller should trace `rayOrigin`/`rayDir`
//           false -> path exhausted; caller may use sample_env(rayDir)
// `landed` reports the block exiting directly into a *different solid* (e.g. glass
// resting on a floor). In that case the caller must shade `landCell` with
// `landNormal` (the glass↔solid interface, against the ray): the surface is
// immediately adjacent, so a fresh trace_voxel would start inside it and could not
// recover the face normal — reading a zero-irradiance interior face (black) instead.
bool transmit_block(inout vec3 rayOrigin, inout vec3 rayDir, ivec3 entryCell,
                    vec3 entryNormal, Mat material, inout vec3 throughput,
                    out ivec3 landCell, out vec3 landNormal, out bool landed) {
    landed = false; landCell = ivec3(-9999); landNormal = vec3(0.0);
    int mediumRow = material_row(entryCell);
    vec3 orientedN = (dot(rayDir, entryNormal) < 0.0) ? entryNormal : -entryNormal;
    vec3 dir = refract(rayDir, orientedN, 1.0 / material.ior);
    if (dot(dir, dir) < 1e-6) {                       // grazing TIR at entry -> reflect
        rayDir = reflect(rayDir, orientedN);
        rayOrigin = rayOrigin + orientedN * EPS;
        return true;
    }
    vec3 origin = rayOrigin + dir * EPS;
    for (int i = 0; i < u_max_trans_depth; i++) {
        float exitDistance; vec3 exitNormal; ivec3 beyondCell; bool exitToEmpty;
        march_medium(origin, dir, mediumRow, exitDistance, exitNormal, beyondCell, exitToEmpty);
        throughput *= exp(-(1.0 - material.albedo) * u_glass_density * exitDistance);
        vec3 exitPoint = origin + dir * exitDistance;
        if (!exitToEmpty) {                           // a different solid behind the block
            rayOrigin = exitPoint;                    // sit on the interface
            rayDir = dir;
            landCell = beyondCell;
            landNormal = -exitNormal;                 // interface face, against the ray
            landed = true;
            return true;
        }
        vec3 outDir = refract(dir, -exitNormal, material.ior);   // glass -> air
        if (dot(outDir, outDir) < 1e-6) {             // total internal reflection
            dir = reflect(dir, exitNormal);
            origin = exitPoint + dir * EPS;
            continue;
        }
        rayOrigin = exitPoint + outDir * EPS;
        rayDir = outDir;
        return true;
    }
    rayOrigin = origin;
    rayDir = dir;
    return false;
}

// Outgoing radiance toward the camera for an opaque metallic-roughness surface,
// as a Cook-Torrance microfacet path:
//
//   Lo = emission
//      + (1 - F) (1 - metallic) albedo · E_cache          (Lambertian diffuse)
//      + ∫ f_spec · L_i · (N·L) dω                         (GGX specular)
//
// The diffuse term reads the converged per-face irradiance cache E_cache, so it
// is noise-free and needs no rays. The specular term is estimated by importance-
// sampling the GGX normal-distribution: one microfacet normal H ~ D(H)(N·H) per
// frame, L = reflect(V, H). For that sampling the estimator reduces to
//   throughput *= F(V·H) · G(N·V,N·L) · (V·H) / (N·V · N·H).
// Samples whose reflected direction falls below the horizon carry no energy and
// simply terminate the path — there is deliberately NO perfect-mirror fallback,
// which is what previously sprayed sharp reflections onto rough/matte surfaces.
// Roughness therefore controls reflection sharpness honestly: ~0 is a mirror, ~1
// scatters so widely the specular reads as a faint sheen. A specular ray that
// lands on glass is passed through the block so reflections stay transparent.
vec3 shade(vec3 hitPoint, vec3 rayDir, ivec3 cell, vec3 normal, Mat material, inout uint rng) {
    vec3 radiance = vec3(0.0);
    vec3 throughput = vec3(1.0);
    for (int bounce = 0; bounce < u_max_bounces; bounce++) {
        radiance += throughput * material.emission;

        // -- dielectric (glass): Fresnel reflect-or-refract through the block ----
        // Done at the top of the loop, so a ray that passes through one glass
        // surface and meets ANOTHER (e.g. the far wall of a glass shell, or glass
        // resting on glass) keeps transmitting instead of being shaded as if that
        // second surface were opaque (which returned the glass's flat albedo and
        // dead-ended bouncy paths to black).
        if (material.transmission > 0.0) {
            vec3 origin, dir;
            if (random01(rng) < fresnel_dielectric(abs(dot(rayDir, normal)), material.ior)) {
                dir = reflect(rayDir, normal);              // mirror off the surface
                origin = hitPoint + normal * EPS;
            } else {                                        // transmit through the block
                origin = hitPoint; dir = rayDir;
                ivec3 landCell; vec3 landNormal; bool landed;
                if (!transmit_block(origin, dir, cell, normal, material, throughput,
                                    landCell, landNormal, landed)) {
                    radiance += throughput * sample_env(dir); break;
                }
                if (landed) {                               // exited onto an adjacent solid
                    hitPoint = origin; cell = landCell; normal = landNormal;
                    material = fetch_material(landCell); rayDir = dir;
                    continue;                               // shade/transmit it next iteration
                }
            }
            ivec3 hc; vec3 hn; float ht;
            if (!trace_voxel(origin + dir * EPS, dir, cell, false, hc, hn, ht)) {
                radiance += throughput * sample_env(dir); break;
            }
            hitPoint = origin + dir * EPS + dir * ht;
            cell = hc; normal = hn; material = fetch_material(hc); rayDir = dir;
            continue;
        }

        // -- opaque metallic-roughness surface ----------------------------------
        vec3 view = -rayDir;
        float ndv = max(dot(normal, view), 1e-4);
        vec3 fresnelF0 = mix(vec3(0.04), material.albedo, material.metallic);

        // Lambertian diffuse from the irradiance cache. Energy-split by the view
        // Fresnel and metalness so it does not double-count the specular below.
        vec3 viewFresnel = fresnel_schlick(ndv, fresnelF0);
        vec3 diffuse = (1.0 - viewFresnel) * (1.0 - material.metallic) * material.albedo;
        radiance += throughput * diffuse
                  * texelFetch(u_gi, atlas_coord(cell, face_of(normal)), 0).rgb * u_gi_scale;

        // GGX specular: importance-sample a microfacet normal and reflect about it.
        vec3 microNormal = ggx_sample(normal, material.roughness, rng);
        vec3 lightDir = reflect(rayDir, microNormal);
        if (dot(lightDir, normal) <= 0.0) break;            // sample below horizon -> no specular
        float ndl = max(dot(normal, lightDir), 1e-4);
        float ndh = max(dot(normal, microNormal), 1e-4);
        float vdh = max(dot(view, microNormal), 1e-4);
        vec3 weight = fresnel_schlick(vdh, fresnelF0)
                    * smith_g(ndv, ndl, material.roughness) * vdh / (ndv * ndh);
        throughput *= min(weight, vec3(4.0));               // firefly clamp on the grazing spike
        if (max(throughput.r, max(throughput.g, throughput.b)) < 0.01) break;

        ivec3 nextCell; vec3 nextNormal; float nextDistance;
        if (!trace_voxel(hitPoint + normal * EPS, lightDir, cell, false, nextCell, nextNormal, nextDistance)) {
            radiance += throughput * sample_env(lightDir);  // reflection escapes to the sky
            break;
        }
        // Advance to the hit; if it is glass, the loop top transmits through it.
        hitPoint = hitPoint + normal * EPS + lightDir * nextDistance;
        cell = nextCell; normal = nextNormal;
        material = fetch_material(nextCell); rayDir = lightDir;
    }
    return radiance;
}

// Deterministic denoiser guide for a glass pixel: follow the refracted ray to
// the first surface behind the block and report its albedo + normal. Without
// this the À-Trous filter, guided by the flat glass surface, blurs the refracted
// image into a uniform smear — which reads as "no refraction".
void dielectric_guide(vec3 entryPoint, vec3 rayDir, ivec3 entryCell, vec3 entryNormal,
                      Mat material, out vec3 guideAlbedo, out vec3 guideNormal) {
    vec3 orientedN = (dot(rayDir, entryNormal) < 0.0) ? entryNormal : -entryNormal;
    vec3 dir = refract(rayDir, orientedN, 1.0 / material.ior);
    if (dot(dir, dir) < 1e-6) { guideAlbedo = material.albedo; guideNormal = entryNormal; return; }
    float exitDistance; vec3 exitNormal; ivec3 beyondCell; bool exitToEmpty;
    march_medium(entryPoint + dir * EPS, dir, material_row(entryCell),
                 exitDistance, exitNormal, beyondCell, exitToEmpty);
    vec3 exitPoint = entryPoint + dir * EPS + dir * exitDistance;
    vec3 outDir = exitToEmpty ? refract(dir, -exitNormal, material.ior) : dir;
    if (dot(outDir, outDir) < 1e-6) outDir = reflect(dir, exitNormal);
    ivec3 gc; vec3 gn; float gt;
    if (trace_voxel(exitPoint + outDir * EPS, outDir, beyondCell, false, gc, gn, gt)) {
        guideAlbedo = fetch_material(gc).albedo;
        guideNormal = gn;
    } else {
        guideAlbedo = vec3(1.0);        // sky behind the glass
        guideNormal = outDir;           // per-pixel distinct -> filter keeps the lensing
    }
}

void main() {
    uint rng = uint(gl_FragCoord.x) * 1973u + uint(gl_FragCoord.y) * 9277u
             + uint(u_frame) * 26699u + 1u;
    vec2 jitter = vec2(random01(rng), random01(rng)) - 0.5;
    vec2 ndc = ((gl_FragCoord.xy + jitter) / u_resolution) * 2.0 - 1.0;
    vec3 rayDir = normalize(u_forward
                          + u_right * (ndc.x * u_aspect * u_tan_half_fov)
                          + u_up * (ndc.y * u_tan_half_fov));
    vec3 rayOrigin = u_eye;

    vec3 color;
    vec3 gAlbedo = vec3(1.0);
    vec3 gNormal = vec3(0.0);
    ivec3 cell; vec3 normal; float hitDistance;
    if (!trace_voxel(rayOrigin, rayDir, ivec3(-9999), false, cell, normal, hitDistance)) {
        color = sample_env(rayDir);
    } else {
        Mat material = fetch_material(cell);
        vec3 hitPoint = rayOrigin + rayDir * hitDistance;
        // shade() handles glass and opaque uniformly; the G-buffer guide for a
        // glass pixel still reports the surface seen through it (for the denoiser).
        if (material.transmission > 0.0) {
            dielectric_guide(hitPoint, rayDir, cell, normal, material, gAlbedo, gNormal);
        } else {
            gAlbedo = material.albedo; gNormal = normal;
        }
        color = shade(hitPoint, rayDir, cell, normal, material, rng);
    }

    // texelFetch (not texture(v_uv)): the accum buffer is NEAREST and same-sized,
    // so an exact integer fetch avoids any half-texel sampling drift on reuse.
    vec3 prev = (u_frame == 0) ? vec3(0.0)
                               : texelFetch(u_accum, ivec2(gl_FragCoord.xy), 0).rgb;
    f_color = vec4(prev + max(color, vec3(0.0)), 1.0);
    f_albedo = vec4(gAlbedo, 1.0);
    f_normal = vec4(gNormal * 0.5 + 0.5, 1.0);
}
