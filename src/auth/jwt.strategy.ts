import { Injectable, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { PassportStrategy } from '@nestjs/passport';
import { ExtractJwt, Strategy } from 'passport-jwt';
import { SupabaseService } from '../supabase/supabase.service';

export interface JwtPayload {
  sub: string;
  email: string;
  role?: string;
}

@Injectable()
export class JwtStrategy extends PassportStrategy(Strategy) {
  constructor(
    private configService: ConfigService,
    private supabase: SupabaseService,
  ) {
    super({
      jwtFromRequest: ExtractJwt.fromAuthHeaderAsBearerToken(),
      ignoreExpiration: false,
      secretOrKey: configService.get<string>('JWT_SECRET') || 'docuintel-default-jwt-secret-2026',
    });
  }

  async validate(payload: JwtPayload) {
    const user = await this.supabase.findUserById(payload.sub);
    if (!user) {
      return { id: payload.sub, email: payload.email, role: payload.role || 'user' };
    }
    const { password_hash, ...safeUser } = user;
    return safeUser;
  }
}

