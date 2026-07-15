USE [INTECEDUCONTINUA];
GO

IF OBJECT_ID(N'[edu].[CalificacionCorte]', N'U') IS NOT NULL
BEGIN
    IF EXISTS (
        SELECT 1
        FROM sys.check_constraints
        WHERE [name] = N'CK_edu_CalificacionCorte_Nota'
          AND [parent_object_id] = OBJECT_ID(N'[edu].[CalificacionCorte]')
    )
    BEGIN
        ALTER TABLE [edu].[CalificacionCorte]
        DROP CONSTRAINT [CK_edu_CalificacionCorte_Nota];
    END;

    ALTER TABLE [edu].[CalificacionCorte] WITH CHECK
    ADD CONSTRAINT [CK_edu_CalificacionCorte_Nota]
    CHECK ([NotaFinal] >= 0 AND [NotaFinal] <= 10);
END;
GO

CREATE OR ALTER PROCEDURE [edu].[usp_RegistrarNotaFinalCorte]
    @EstudianteCorteId int,
    @NotaFinal decimal(4,2),
    @UsuarioRegistro varchar(50) = NULL,
    @Observacion nvarchar(500) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    IF @NotaFinal < 0 OR @NotaFinal > 10
        THROW 53170, 'La nota final debe estar en el rango de 0.00 a 10.00.', 1;

    DECLARE @CorteId int, @CodigoEstud decimal(18,0);
    SELECT @CorteId = [CorteId], @CodigoEstud = [CodigoEstud]
    FROM [edu].[CorteEstudiante]
    WHERE [EstudianteCorteId] = @EstudianteCorteId;

    IF @CorteId IS NULL
        THROW 53171, 'No se encontro la matricula del estudiante.', 1;

    IF EXISTS (SELECT 1 FROM [edu].[CalificacionCorte] WHERE [EstudianteCorteId] = @EstudianteCorteId)
    BEGIN
        UPDATE [edu].[CalificacionCorte]
        SET [NotaFinal] = @NotaFinal,
            [EstadoNota] = CASE WHEN [EstadoNota] IN ('PASADA','CERRADA') THEN [EstadoNota] ELSE 'BORRADOR' END,
            [UsuarioModifica] = @UsuarioRegistro,
            [FechaModifica] = sysdatetime(),
            [Observacion] = @Observacion
        WHERE [EstudianteCorteId] = @EstudianteCorteId;
    END
    ELSE
    BEGIN
        INSERT INTO [edu].[CalificacionCorte]
        ([EstudianteCorteId], [CorteId], [CodigoEstud], [NotaFinal], [UsuarioRegistro], [Observacion])
        VALUES
        (@EstudianteCorteId, @CorteId, @CodigoEstud, @NotaFinal, @UsuarioRegistro, @Observacion);
    END

    SELECT * FROM [edu].[CalificacionCorte] WHERE [EstudianteCorteId] = @EstudianteCorteId;
END;
GO
